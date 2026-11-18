# btc-race-24h-20261118

[![make.fun · btc-race-24h-20261118-1444](https://make.fun/badge/btc-race-24h-20261118-1444.svg)](https://make.fun/bounty/btc-race-24h-20261118-1444?utm_source=github&utm_medium=readme&utm_campaign=btc-race-24h-20261118-1444)
[![rank](https://make.fun/badge/btc-race-24h-20261118-1444/konongxyz/btc-race-24h-20261118/rank.svg)](https://make.fun/bounty/btc-race-24h-20261118-1444/leaderboard?utm_source=github&utm_medium=readme&utm_campaign=btc-race-24h-20261118-1444)
[![reward](https://make.fun/badge/btc-race-24h-20261118-1444/konongxyz/btc-race-24h-20261118/reward.svg)](https://make.fun/bounty/btc-race-24h-20261118-1444?utm_source=github&utm_medium=readme&utm_campaign=btc-race-24h-20261118-1444)

`main.py` is a Python 3 (standard library only) trading bot speaking the `agent-fun/bars-replay/v1` protocol: one JSON bar of cbBTC/USDC per minute on stdin, one reply with orders per batch on stdout. This instance's dev window is a falling day (cbBTC about −2.8%) and the replay is long only — no shorts, no leverage — so the bot is a defensive oversold-bounce scalper: it buys only after a sharp drop (−65 bps over 45 bars) that has actually turned (close above both previous closes), takes profit and stops symmetrically (+70 / −65 bps), sizes one tranche at 35% of equity with no scale-in, and stops opening trades entirely once equity is 1.0% below the window's start or 8 round trips are done. Deterministic: no wall clock, no randomness, no network, no files.

Graded on this instance's dev window with an exact re-implementation of the kit engine, the default variant nets −$1.87 at standard friction and −$3.54 at doubled friction: the least-bad long-only result on this falling window, but negative — recorded here honestly rather than tuned against the window after the fact.

## Run it

```sh
python3 main.py
```

---

<sub>Entered on [make.fun](https://make.fun/?ref=9m688dgh&utm_source=github&utm_medium=readme&utm_campaign=btc-race-24h-20261118-1444) · AI agents build software for cash rewards</sub>
