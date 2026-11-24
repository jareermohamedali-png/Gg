"""Open-trade monitor: tells you when an open trade needs action.

Stateless: every run rebuilds the trade's history from hourly candles since the fill time, then reports
only the events that happened in the last `--window` minutes (so an hourly check never repeats itself).

Events: stop hit · warning (4h candle closed beyond a warning level) · TP1 hit (take 1/3, move stop to
entry) · TP2 hit · TP3 hit (trade done) · break-even stop hit after TP1.

  python -m trade_analyzer.monitor AUDJPY SELL --entry 105.70 --stop 106.25 \
      --tp 104.80 104.05 102.32 --filled "2026-11-24 06:00" --warn 106.00
Exit code 0 = nothing new, 10 = action needed (message printed on the first line), 11 = trade finished.
"""
import argparse
import sys

import pandas as pd
import requests

from .data import fetch_yahoo
from .forex import parse


def live_price(pair):
    try:
        r = requests.post("https://scanner.tradingview.com/forex/scan", timeout=15,
                          json={"symbols": {"tickers": [f"OANDA:{pair}"]}, "columns": ["close"]},
                          headers={"User-Agent": "Mozilla/5.0"})
        return float(r.json()["data"][0]["d"][0])
    except Exception:
        return None


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("pair")
    p.add_argument("side", choices=["BUY", "SELL", "buy", "sell"])
    p.add_argument("--entry", type=float, required=True)
    p.add_argument("--stop", type=float, required=True)
    p.add_argument("--tp", type=float, nargs=3, required=True)
    p.add_argument("--filled", required=True, help="fill time, UTC")
    p.add_argument("--warn", type=float, help="warn if a 4h candle closes beyond this level")
    p.add_argument("--window", type=int, default=65, help="report events from the last N minutes")
    p.add_argument("--pending", action="store_true", help="limit order not filled yet: wait for price to reach --entry")
    p.add_argument("--expire", help="pending order: alert to cancel if not filled by this UTC time")
    p.add_argument("--state", help="file remembering alerts already sent (enables frequent polling without repeats)")
    a = p.parse_args(argv)
    d = 1 if a.side.upper() == "BUY" else -1
    fx = parse(a.pair)
    pip = fx["pip"] if fx else 0.0001
    pips = lambda x: f"{d * (x - a.entry) / pip:+.0f} pips"

    df = fetch_yahoo(fx["yahoo"] if fx else a.pair, "1h")
    filled = pd.Timestamp(a.filled, tz="UTC")
    bars = df[df.index >= filled.floor("h")]
    now = pd.Timestamp.now(tz="UTC")
    recent = now - pd.Timedelta(minutes=a.window)
    px = live_price(fx["pair"] if fx else a.pair) or float(df["close"].iloc[-1])

    events, stop, tp_hit, done = [], a.stop, 0, False
    if a.pending:
        hit = bars[(bars["high"] >= a.entry) if d == -1 else (bars["low"] <= a.entry)]
        if hit.empty:
            if a.expire and now >= pd.Timestamp(a.expire, tz="UTC"):
                events.append((now, f"{a.pair} limit {a.entry} NOT FILLED by {a.expire} UTC: cancel the order "
                                    f"(price now {px}). Don't chase it."))
                done = True
            bars = bars.iloc[0:0]
        else:
            t0 = hit.index[0]
            events.append((t0, f"{a.pair} limit FILLED at {a.entry}: trade is live. Stop {a.stop}, "
                               f"TP1 {a.tp[0]}, TP2 {a.tp[1]}, TP3 {a.tp[2]}. Check SL/TP are set at your broker."))
            bars = bars[bars.index >= t0]
    for t, b in bars.iterrows():
        hi, lo = b["high"], b["low"]
        if (d == -1 and hi >= stop) or (d == 1 and lo <= stop):
            label = "STOP HIT" if tp_hit == 0 else "BREAK-EVEN STOP HIT"
            events.append((t, f"{a.pair} {label} at {stop} - trade closed ({pips(stop)}). Check your broker closed it."))
            done = True
            break
        while tp_hit < 3 and ((d == -1 and lo <= a.tp[tp_hit]) or (d == 1 and hi >= a.tp[tp_hit])):
            tp = a.tp[tp_hit]
            tp_hit += 1
            if tp_hit == 1:
                msg = f"{a.pair} TP1 {tp} hit ({pips(tp)}): close 1/3 now and move stop to entry {a.entry}."
                stop = a.entry
            elif tp_hit == 2:
                msg = f"{a.pair} TP2 {tp} hit ({pips(tp)}): close another 1/3, keep stop at {a.entry} for the rest."
            else:
                msg = f"{a.pair} TP3 {tp} hit ({pips(tp)}): close the last 1/3. Trade complete."
                done = True
            events.append((t, msg))
        if done:
            break

    if a.warn and not done:
        h4 = bars.resample("4h", origin="epoch").agg({"open": "first", "close": "last"}).dropna()
        h4 = h4[h4.index + pd.Timedelta(hours=4) <= now]            # closed candles only
        for t, b in h4.iterrows():
            if d * (b["close"] - a.warn) < 0 and d * (b["close"] - b["open"]) < 0:
                events.append((t + pd.Timedelta(hours=4),
                               f"{a.pair} WARNING: 4h candle closed at {b['close']:.3f}, beyond {a.warn} against you "
                               f"({pips(b['close'])}). Consider closing; stop is {stop}."))

    if a.state:
        import os
        seen = set(open(a.state).read().splitlines()) if os.path.exists(a.state) else set()
        new = [m for t, m in events if m.split(":")[0] not in seen and t + pd.Timedelta(hours=24) >= now]
        with open(a.state, "a") as f:
            for m in new:
                f.write(m.split(":")[0] + "\n")
    else:
        new = [m for t, m in events if t + pd.Timedelta(hours=1) >= recent]
    if a.pending and not len(bars) and not done:
        status_pending = f"{a.pair} {a.side.upper()} LIMIT {a.entry} pending, price {px}"
    else:
        status_pending = None
    status = status_pending or (f"{a.pair} {a.side.upper()} @ {a.entry}  price {px}  ({pips(px)})  stop {stop}  "
              f"TPs hit {tp_hit}/3  {'CLOSED' if done else 'open'}")
    if new:
        print(" | ".join(new))
        print(status)
        return 11 if done else 10
    print("NO ACTION - " + status)
    return 11 if done else 0


if __name__ == "__main__":
    sys.exit(main())
