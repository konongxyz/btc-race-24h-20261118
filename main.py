#!/usr/bin/env python3
"""Down-window long-only candidate for btc-race-24h-20261118-1444
(make.fun, @konongsz).

Why this bot exists: this instance's dev window is a falling day
(cbBTC about -2.8% open to close) and the replay is LONG ONLY - the
kit's protocol fixes caps.shorts to false, the rules say "long only:
no shorts, no leverage", and the engine refuses any sell larger than
the quantity held. On a falling day a long-only bot cannot ride the
trend down; the only honest ways to finish positive are to catch
oversold bounces, keep each loss small and symmetric, and otherwise
sit in cash. The four standing candidates were all graded on this
window and all lost (best -2.11), because they either buy falling
dips without a turn confirmation, size at 55%, or let one stop cost
more than several wins.

This family is built against exactly those failure modes:

  - Entry needs BOTH a real oversold move (trailing return at or
    below -DIP_BPS over LOOKBACK bars) AND a turn: the current close
    must be above the previous close (variant C: above the closes of
    both of the last two bars). No turn, no trade - the bot refuses
    to catch a knife that is still falling.
  - Exits are symmetric scalps: take-profit and hard stop are the
    same order of size (TP_BPS vs SL_BPS), plus a short timeout, so
    no single trade can cost more than a win pays.
  - One tranche only, sized at 30-40% of equity. No scale-in: adding
    to a loser is how the dip-buyers bled on the falling 17 Nov race.
  - A circuit breaker guards the dev gate itself: once equity is
    1.0% below the window's starting equity, or MAX_TRADES round
    trips have completed, the bot stops opening positions for the
    rest of the window. A bad day is capped near -1% instead of
    compounding.

Three variants were declared before any grading (no tuning sweep):
  A: lookback 30, dip -50 bps, TP +60 / stop -60, hold 60, 35% size,
     one-bar turn.
  B: lookback 60, dip -85 bps, TP +85 / stop -70, hold 90, 40% size,
     one-bar turn. Deeper, rarer, wider.
  C: lookback 45, dip -65 bps, TP +70 / stop -65, hold 75, 35% size,
     two-bar turn (stricter confirmation).
The variant is chosen by the BTCBOT_VARIANT environment variable,
defaulting to variant C below; the graded entry runs with no
environment set.

GRADING VERDICT on this instance's dev window (2026-11-18, exact
harness): A -5.32 / -8.64, B -8.53 / -9.83, C -1.87 / -3.54 at
1x / 2x friction. NO variant passed the submit gate (positive at
both frictions): C is the least-bad long-only design tried on this
window and is kept as the recorded candidate, NOT as a submit-ready
entry. See btc-race-next-spec.md for the full table.

The bot speaks newline-delimited JSON on stdin/stdout: a hello, one
batch per minute bar, then done. It answers every batch with exactly
one reply. Fully deterministic: no clock, no randomness, no files,
no network.
"""

import json
import os
import sys

SYMBOL = "CBBTC"

# --- declared variants (fixed before grading; see module docstring) -----
VARIANTS = {
    "A": {"lookback": 30, "dip_bps": 50, "tp_bps": 60, "sl_bps": 60,
          "max_hold": 60, "share": 0.35, "turn_bars": 1},
    "B": {"lookback": 60, "dip_bps": 85, "tp_bps": 85, "sl_bps": 70,
          "max_hold": 90, "share": 0.40, "turn_bars": 1},
    "C": {"lookback": 45, "dip_bps": 65, "tp_bps": 70, "sl_bps": 65,
          "max_hold": 75, "share": 0.35, "turn_bars": 2},
}
DEFAULT_VARIANT = "C"

# --- chassis constants (shared by every variant) -------------------------
COOLDOWN_BARS = 10        # batches to sit out after a filled exit
HALT_DD_BPS = 100         # stop opening trades once equity is this far
                          # below the window's starting equity
MAX_TRADES = 8            # hard cap on completed round trips per window
SLIPPAGE_CAP_BPS = 300    # max_slippage_bps sent with every order


class Bot:
    """All state the strategy keeps between batches."""

    def __init__(self, cfg):
        self.cfg = cfg
        self.closes = []          # raw integer closes, oldest first
        self.min_notional = 1_000_000
        self.warmup_bars = 0
        self.seq = -1
        self.start_equity = None  # equity on the first batch seen
        # position tracking: fill-weighted average entry price (raw
        # price units) of the open position, and its quantity.
        self.ref_price = None
        self.ref_qty = 0
        self.hold_bars = 0
        self.cooldown = 0
        self.trades_done = 0      # completed round trips
        self.pending = None       # (client_id, side) awaiting its report
        self.pending_age = 0
        self.order_no = 0

    # -- wire helpers ------------------------------------------------------
    def next_id(self, tag):
        self.order_no += 1
        return "%s%d" % (tag, self.order_no)

    # -- fill bookkeeping ----------------------------------------------------
    def note_fills(self, fills, account_qty):
        for f in fills:
            if self.pending is None or f.get("client_id") != self.pending[0]:
                continue
            side = self.pending[1]
            self.pending = None
            self.pending_age = 0
            if f.get("status") != "filled":
                continue
            if side == "buy":
                price = int(f["price"])
                qty = int(f["qty"])
                if self.ref_price is None or self.ref_qty == 0:
                    self.ref_price = float(price)
                    self.ref_qty = qty
                else:
                    tot = self.ref_qty + qty
                    self.ref_price = (self.ref_price * self.ref_qty
                                      + price * qty) / tot
                    self.ref_qty = tot
                self.hold_bars = 0
            else:  # a filled sell closes the one position we ever hold
                self.ref_price = None
                self.ref_qty = 0
                self.hold_bars = 0
                self.trades_done += 1
                self.cooldown = COOLDOWN_BARS
        if account_qty == 0 and self.pending is None and self.ref_price is not None:
            # Position vanished without our sell naming it (window-end
            # flatten): reset the books so nothing stale is traded on.
            self.ref_price = None
            self.ref_qty = 0
            self.hold_bars = 0

    # -- the strategy --------------------------------------------------------
    def decide(self, batch, account):
        """Return the order list for this batch (empty or one order)."""
        bars = batch.get("bars") or []
        bar = None
        for b in bars:
            if b.get("symbol") == SYMBOL:
                bar = b
        positions = account.get("positions") or []
        held_qty = 0
        for p in positions:
            if p.get("symbol") == SYMBOL:
                held_qty = int(p["qty"])
        self.note_fills(batch.get("fills") or [], held_qty)

        if self.pending is not None:
            self.pending_age += 1
            if self.pending_age > 2:
                # The fill report never named our order; stop waiting.
                self.pending = None
                self.pending_age = 0
        if bar is None:
            return []
        close = int(bar["c"])
        self.closes.append(close)
        if self.seq < self.warmup_bars:
            return []
        if self.cooldown > 0:
            self.cooldown -= 1

        cash = int(account["cash_quote_units"])
        equity = int(account["equity_quote_units"])
        if self.start_equity is None:
            self.start_equity = equity

        if held_qty > 0:
            self.hold_bars += 1
            if self.pending is not None:
                # Last batch's order has not reported back; wait for it
                # instead of stacking another order on the position.
                return []
            if self.ref_price is None:
                # Holding something our fills never priced (cannot
                # happen in practice); anchor on the close and carry on.
                self.ref_price = float(close)
                self.ref_qty = held_qty
            ref = self.ref_price
            if close <= ref * (1.0 - self.cfg["sl_bps"] / 10000.0):
                return [self.sell_order(held_qty)]
            if close >= ref * (1.0 + self.cfg["tp_bps"] / 10000.0):
                return [self.sell_order(held_qty)]
            if self.hold_bars >= self.cfg["max_hold"]:
                return [self.sell_order(held_qty)]
            return []

        # Flat: entries only through every gate, in order.
        if self.pending is not None or self.cooldown > 0:
            return []
        if self.trades_done >= MAX_TRADES:
            return []  # trade budget spent; protect the standing result
        if equity <= self.start_equity * (1.0 - HALT_DD_BPS / 10000.0):
            return []  # circuit breaker: the day is not offering bounces
        lb = self.cfg["lookback"]
        if len(self.closes) <= lb:
            return []
        then = self.closes[-1 - lb]
        if then <= 0:
            return []
        dip = (close / then - 1.0) * 10000.0
        if dip > -self.cfg["dip_bps"]:
            return []  # not oversold enough
        # Turn confirmation: the fall must have stopped, on closes.
        if close <= self.closes[-2]:
            return []
        if self.cfg["turn_bars"] >= 2 and close <= self.closes[-3]:
            return []
        notional = int(equity * self.cfg["share"])
        if notional > cash:
            notional = cash
        if notional < self.min_notional:
            return []
        return [self.buy_order(notional)]

    def buy_order(self, notional):
        cid = self.next_id("b")
        self.pending = (cid, "buy")
        self.pending_age = 0
        return {"side": "buy", "client_id": cid, "symbol": SYMBOL,
                "notional_quote_units": str(notional),
                "max_slippage_bps": SLIPPAGE_CAP_BPS}

    def sell_order(self, qty):
        cid = self.next_id("s")
        self.pending = (cid, "sell")
        self.pending_age = 0
        return {"side": "sell", "client_id": cid, "symbol": SYMBOL,
                "qty": str(qty), "max_slippage_bps": SLIPPAGE_CAP_BPS}


def main():
    name = os.environ.get("BTCBOT_VARIANT", DEFAULT_VARIANT)
    bot = Bot(VARIANTS.get(name, VARIANTS[DEFAULT_VARIANT]))
    out = sys.stdout
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        msg = json.loads(line)
        kind = msg.get("type")
        if kind == "hello":
            bot.min_notional = int(msg["fees"]["min_notional_quote_units"])
            bot.warmup_bars = int(msg.get("warmup_bars") or 0)
        elif kind == "batch":
            bot.seq = int(msg["seq"])
            orders = bot.decide(msg, msg["account"])
            reply = {"type": "reply", "seq": msg["seq"], "orders": orders}
            out.write(json.dumps(reply, separators=(",", ":")) + "\n")
            out.flush()
        elif kind == "done":
            return


if __name__ == "__main__":
    main()
