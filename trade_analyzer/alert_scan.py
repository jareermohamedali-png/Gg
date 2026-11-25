"""Scan a watchlist and report NEW setups at or above a score threshold (for push alerts).

  python -m trade_analyzer.alert_scan forex -i 1d 4h --min-score 75 --state ~/.trade_monitor/scan.json
Exit code 0 = nothing new, 10 = new setup(s) found (one alert per line, then details).
A setup is remembered by (pair, timeframe, side, strategy) and not re-alerted for --cooldown hours.
Limit-order setups are only reported when the entry is within --max-dist ATR of the current price.

--edge CSV [CSV ...] filters by history: a setup is only reported when past setups of the same pair and timeframe
at or above --min-score (from backtest --csv output) averaged at least --edge-min R per trade at TP1, over at
least --edge-n trades. Alerts then show that history; "PROVEN" means the average stays positive after
subtracting 2 standard errors. Choosing pairs by their past results is itself a form of curve fitting.
"""
import argparse
import glob
import json
import os
import sys
import time

import requests

from . import report, setups
from .analysis import analyze
from .data import load
from .forex import decimals, expand, parse


def tv_prices(pairs):
    out = {}
    for market, group in (("forex", [p for p in pairs if not p.startswith("XA")]),
                          ("cfd", [p for p in pairs if p.startswith("XA")])):   # metals live on the CFD scanner
        if not group:
            continue
        try:
            r = requests.post(f"https://scanner.tradingview.com/{market}/scan", timeout=15,
                              json={"symbols": {"tickers": [f"OANDA:{p}" for p in group]}, "columns": ["close"]},
                              headers={"User-Agent": "Mozilla/5.0"})
            out.update({d["s"].split(":")[1]: d["d"][0] for d in r.json().get("data", [])})
        except Exception:
            pass
    return out


def load_edge(a):
    """{(pair, interval): history text} for pairs whose past A-grade setups were profitable at TP1."""
    import numpy as np
    import pandas as pd
    files = [f for pat in a.edge for f in glob.glob(os.path.expanduser(pat))]
    df = pd.concat([pd.read_csv(f) for f in files])
    df = df[df["score"] >= a.min_score]
    out = {}
    for (sym, iv), g in df.groupby(["symbol", "interval"]):
        r = g["TP1"]
        n, m = len(r), r.mean()
        if n < a.edge_n or m < a.edge_min:
            continue
        lo = m - 2 * r.std() / np.sqrt(n)
        out[(sym, iv)] = (f" | history: {n} trades, {100 * (r > 0).mean():.0f}% hit TP1, avg {m:+.2f}R"
                          + (" PROVEN" if lo > 0 else " (could be luck)"))
    return out


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("symbols", nargs="+")
    p.add_argument("-i", "--interval", nargs="+", default=["1d", "4h"])
    p.add_argument("--min-score", type=int, default=75)
    p.add_argument("--max-dist", type=float, default=0.5, help="max distance of a limit entry, in ATR")
    p.add_argument("--state", default=os.path.expanduser("~/.trade_monitor/scan.json"))
    p.add_argument("--cooldown", type=float, default=24)
    p.add_argument("--skip", nargs="*", default=[], help="PAIR:SIDE already being traded, e.g. EURCAD:SELL")
    p.add_argument("--edge", nargs="*", help="backtest CSV(s) used to keep only pairs with a positive history")
    p.add_argument("--edge-min", type=float, default=0.2, help="min average R at TP1 for --edge")
    p.add_argument("--edge-n", type=int, default=15, help="min number of past trades for --edge")
    a = p.parse_args(argv)
    edge = load_edge(a) if a.edge else None
    report.USE_COLOR = False
    os.makedirs(os.path.dirname(a.state), exist_ok=True)
    try:
        seen = json.load(open(a.state))
    except Exception:
        seen = {}
    now = time.time()
    seen = {k: v for k, v in seen.items() if now - v < a.cooldown * 3600}

    found = []
    for iv in a.interval:
        for sym in expand(a.symbols):
            fx = parse(sym)
            try:
                df = load(sym, iv)
            except Exception:
                continue
            setups.PRICE_DP = decimals(fx) if fx else None
            ctx = analyze(df, fx["pair"] if fx else sym.upper(), iv)
            ctx["fx"] = fx
            for s in ctx["setups"]:
                if s.score < a.min_score:
                    continue
                if s.entry_type != "market" and abs(s.entry - ctx["px"]) > a.max_dist * ctx["atr"]:
                    continue
                if f"{ctx['symbol']}:{s.side}" in {x.upper() for x in a.skip}:
                    continue
                if edge is not None and (ctx["symbol"], iv) not in edge:
                    continue
                key = f"{ctx['symbol']}|{iv}|{s.side}|{s.strategy}"
                if key in seen:
                    continue
                found.append((ctx, s, key))
            setups.PRICE_DP = None

    if not found:
        json.dump(seen, open(a.state, "w"))
        print(f"NO NEW SETUPS (min score {a.min_score}) at {time.strftime('%H:%M', time.gmtime())} UTC")
        return 0
    live = tv_prices([c["symbol"] for c, _, _ in found])
    found.sort(key=lambda x: -x[1].score)
    lines, details = [], []
    for ctx, s, key in found:
        fx = ctx["fx"]
        setups.PRICE_DP = decimals(fx) if fx else None
        f = setups.fmt
        risk = f"{s.risk / fx['pip']:.0f} pips" if fx else f"{s.risk_pct:.2f}%"
        lines.append(f"{s.grade}-grade {s.side} {ctx['symbol']} ({ctx['interval']}, {s.score}): {s.entry_type} {f(s.entry)}, "
                     f"SL {f(s.stop)} ({risk}), TP1 {f(s.targets[0].price)}"
                     + (edge[(ctx["symbol"], ctx["interval"])] if edge else ""))
        details.append(f"{ctx['symbol']} {ctx['interval']} live TradingView price: {live.get(ctx['symbol'], 'n/a')}"
                       + report.setup_block(s, 1, fx=fx))
        seen[key] = now
        setups.PRICE_DP = None
    json.dump(seen, open(a.state, "w"))
    print("\n".join(lines))
    print("\n".join(details))
    return 10


if __name__ == "__main__":
    sys.exit(main())
