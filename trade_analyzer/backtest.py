"""Walk-forward backtest of the setup rules.

At every bar the analyser only sees data up to that bar (indicators are causal, swing points are only
used once confirmed). Each setup is then simulated forward on later bars:
  * market entries fill at the NEXT bar's open; limit entries get `max_wait` bars to fill or are cancelled
  * if the stop and a target are touched in the same bar, the stop is assumed to come first (conservative)
  * a round-trip spread is charged on every trade
  * a fill that leaves less than half the planned risk (price already ran toward the stop) is skipped
  * trades still open after `max_hold` bars are closed at that bar's close
One position at a time per (symbol, strategy), so the same signal is not counted on consecutive bars.

Results are in R (multiples of the initial risk). Exit styles compared:
  TP1 / TP2 / TP3  - whole position exits at that target
  SCALED           - 1/3 at each target, stop moved to break-even after TP1

Usage:
  python -m trade_analyzer.backtest forex -i 1d
  python -m trade_analyzer.backtest majors -i 4h --min-score 60
"""
import argparse
import math
import sys
from collections import defaultdict

import numpy as np

from . import setups as S
from .analysis import build_context
from .data import load
from .forex import expand, parse
from .indicators import add_indicators
from .structure import find_swings

MODES = ("TP1", "TP2", "TP3", "SCALED")
DEFAULT_HISTORY = {"1d": "10y", "1wk": "max", "4h": "730d", "1h": "730d"}


def spread_for(sym, fx):
    """Typical retail spread in price units (conservative)."""
    if not fx:
        return 0.0
    pair = fx["pair"]
    majors = {"EURUSD": 1.0, "USDJPY": 1.2, "GBPUSD": 1.5, "USDCHF": 1.8, "AUDUSD": 1.4, "USDCAD": 1.8, "NZDUSD": 2.0}
    pips = majors.get(pair, 30 if pair == "XAUUSD" else 30 if pair == "XAGUSD" else 3.0)
    if pair == "XAGUSD":
        return 0.03
    return pips * fx["pip"]


def _fill(s, o, h, l, t, max_wait):
    """Return (fill_bar, entry_price) or None if never filled / invalid."""
    d, n = s.direction, len(o)
    if t + 1 >= n:
        return None
    if s.entry_type == "market":
        e = o[t + 1]
        return (t + 1, e) if d * (e - s.stop) > 0 else None   # gapped through the stop: no trade
    for k in range(t + 1, min(t + 1 + max_wait, n)):
        if d == 1 and l[k] <= s.entry:
            return k, min(o[k], s.entry)
        if d == -1 and h[k] >= s.entry:
            return k, max(o[k], s.entry)
    return None


def _run(s, entry, k0, o, h, l, c, max_hold, mode):
    """Simulate one exit style from fill bar k0. Returns (R result, exit bar)."""
    d, R = s.direction, abs(entry - s.stop)
    tps = [t.price for t in s.targets]
    stop = s.stop
    end = min(k0 + max_hold, len(c) - 1)
    if mode != "SCALED":
        tp = tps[int(mode[-1]) - 1]
        for k in range(k0, end + 1):
            if (d == 1 and l[k] <= stop) or (d == -1 and h[k] >= stop):
                return d * (stop - entry) / R, k
            if (d == 1 and h[k] >= tp) or (d == -1 and l[k] <= tp):
                return d * (tp - entry) / R, k
        return d * (c[end] - entry) / R, end
    rem, pnl, hit = 1.0, 0.0, 0
    for k in range(k0, end + 1):
        if (d == 1 and l[k] <= stop) or (d == -1 and h[k] >= stop):
            return pnl + rem * d * (stop - entry) / R, k
        while hit < 3 and ((d == 1 and h[k] >= tps[hit]) or (d == -1 and l[k] <= tps[hit])):
            pnl += d * (tps[hit] - entry) / R / 3
            rem -= 1 / 3
            hit += 1
            stop = entry          # break-even after the first target
        if hit == 3:
            return pnl, k
    return pnl + rem * d * (c[end] - entry) / R, end


def backtest_symbol(sym, interval, history, min_score=0, max_wait=5, max_hold=None, warmup=250, swing=3, log=None):
    fx = parse(sym)
    df = add_indicators(load(sym, interval, history=history))
    name = fx["pair"] if fx else sym.upper()
    S.PRICE_DP = None
    swings = find_swings(df, swing, swing)
    o, h, l, c = (df[k].to_numpy() for k in ("open", "high", "low", "close"))
    cost = spread_for(sym, fx)
    max_hold = max_hold or (40 if interval in ("1d", "1wk") else 120)
    busy, trades, unfilled = defaultdict(int), [], defaultdict(int)
    si = 0
    confirmed = []
    for t in range(warmup, len(df) - 1):
        while si < len(swings) and swings[si].i <= t - swing:
            confirmed.append(swings[si]); si += 1
        ctx = build_context(df.iloc[:t + 1], confirmed, name, interval)
        for s in ctx["setups"]:
            key = s.strategy
            if s.score < min_score or busy[key] > t:
                continue
            f = _fill(s, o, h, l, t, max_wait)
            if f is None:
                unfilled[key] += 1
                busy[key] = t + (max_wait if s.entry_type != "market" else 1)
                continue
            k0, entry = f
            R = abs(entry - s.stop)
            if R < 0.5 * s.risk:      # price opened most of the way to the stop: a trader would skip it
                unfilled[key] += 1
                continue
            res, last_exit = {}, k0
            for m in MODES:
                r, kx = _run(s, entry, k0, o, h, l, c, max_hold, m)
                res[m] = r - cost / R
                last_exit = max(last_exit, kx)
            busy[key] = last_exit + 1
            trades.append({"symbol": name, "interval": interval, "time": df.index[t], "strategy": s.strategy,
                           "side": s.side, "score": s.score, "grade": s.grade, "entry_type": s.entry_type,
                           "cost_R": cost / R, **res})
    if log:
        log(f"  {name:8} {len(df):5} bars  {len(trades):4} trades  ({sum(unfilled.values())} limit orders unfilled)")
    return trades


def stats(rs):
    rs = np.asarray(rs, float)
    n = len(rs)
    if n == 0:
        return None
    wins, losses = rs[rs > 0].sum(), -rs[rs < 0].sum()
    eq = np.cumsum(rs)
    dd = float(np.max(np.maximum.accumulate(np.r_[0, eq]) - np.r_[0, eq]))
    se = rs.std(ddof=1) / math.sqrt(n) if n > 1 else float("nan")
    return {"n": n, "win": 100 * (rs > 0).mean(), "avg": rs.mean(), "se": se, "total": rs.sum(),
            "pf": wins / losses if losses else float("inf"), "dd": dd}


def verdict(st):
    if st["n"] < 30:
        return "too few trades"
    lo, hi = st["avg"] - 2 * st["se"], st["avg"] + 2 * st["se"]
    if lo > 0:
        return "EDGE (positive beyond noise)"
    if hi < 0:
        return "LOSING (negative beyond noise)"
    return "no proven edge (could be luck)"


def table(trades, by, mode, min_n=1, title=None):
    groups = defaultdict(list)
    for tr in sorted(trades, key=lambda x: x["time"]):
        groups[by(tr)].append(tr[mode])
    rows = [(k, stats(v)) for k, v in groups.items() if len(v) >= min_n]
    rows.sort(key=lambda x: -x[1]["avg"])
    out = [f"\n {title or ''}  [exit: {mode}]",
           f"   {'group':<26}{'trades':>7}{'win%':>7}{'avg R':>8}{'±2se':>7}{'total R':>9}{'PF':>6}{'maxDD R':>9}  verdict"]
    for k, st in rows:
        pf = "inf" if st["pf"] == float("inf") else f"{st['pf']:.2f}"
        out.append(f"   {str(k):<26}{st['n']:>7}{st['win']:>7.1f}{st['avg']:>+8.3f}{2 * st['se']:>7.3f}"
                   f"{st['total']:>+9.1f}{pf:>6}{st['dd']:>9.1f}  {verdict(st)}")
    return "\n".join(out)


def report(trades):
    out = [f"\n Total trades: {len(trades)}"]
    out.append(table(trades, lambda t: "ALL", "TP1", title="Exit style comparison"))
    for m in MODES[1:]:
        out.append(table(trades, lambda t: "ALL", m).split("\n", 3)[-1])
    best = max(MODES, key=lambda m: np.mean([t[m] for t in trades]))
    out.append(table(trades, lambda t: (t["interval"], t["strategy"]), best, title="By timeframe × strategy"))
    out.append(table(trades, lambda t: (t["interval"], t["strategy"], t["side"]), best, 20, title="By timeframe × strategy × side (≥20 trades)"))
    out.append(table(trades, lambda t: (t["interval"], t["grade"]), best, title="By timeframe × grade"))
    out.append(table(trades, lambda t: (t["interval"], t["symbol"]), best, 15, title="By timeframe × pair (≥15 trades)"))
    return "\n".join(out)


def main(argv=None):
    p = argparse.ArgumentParser(description="Walk-forward backtest of trade_analyzer setups")
    p.add_argument("symbols", nargs="+")
    p.add_argument("-i", "--interval", nargs="+", default=["1d"])
    p.add_argument("--history", help="Yahoo range, e.g. 10y, 5y, 730d (default by interval)")
    p.add_argument("--min-score", type=int, default=0)
    p.add_argument("--csv", help="write every simulated trade to this CSV")
    a = p.parse_args(argv)
    trades = []
    for iv in a.interval:
        print(f"\n== {iv} ==")
        for sym in expand(a.symbols):
            try:
                trades += backtest_symbol(sym, iv, a.history or DEFAULT_HISTORY.get(iv), a.min_score, log=print)
            except Exception as e:
                print(f"  {sym}: skipped ({e})")
    if not trades:
        print("No trades.")
        return 1
    if a.csv:
        import pandas as pd
        pd.DataFrame(trades).to_csv(a.csv, index=False)
    print(report(trades))
    print("\n R = initial risk. avg R > 0 means profitable after spread. '±2se' is the noise band: an edge is only "
          "credible if avg R minus that band is still above 0.\n Past results do not guarantee future results.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
