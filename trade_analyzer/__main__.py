"""Command line: python -m trade_analyzer SYMBOL [SYMBOL ...] [options]"""
import argparse
import os
import sys

from . import report, setups
from .analysis import analyze
from .data import fx_rate, load
from .forex import decimals, expand, parse

DISCLAIMER = ("Educational tool - technical analysis only, not financial advice. Setups are probabilistic; "
              "always use a stop loss and size positions so a loss is affordable.")


def main(argv=None):
    p = argparse.ArgumentParser(prog="trade_analyzer", description="Analyse charts and rank BUY/SELL setups.",
                                formatter_class=argparse.RawDescriptionHelpFormatter, epilog="""examples:
  python -m trade_analyzer forex                      (scan majors, minors, gold & silver)
  python -m trade_analyzer majors -i 4h --account 5000 --risk 1
  python -m trade_analyzer EURUSD GBPJPY XAUUSD -i 1h
  python -m trade_analyzer AAPL MSFT BTC-USD
  python -m trade_analyzer BTCUSDT ETHUSDT -i 4h --account 10000 --risk 1
  python -m trade_analyzer my_export.csv --chart charts/
  python -m trade_analyzer demo1 demo2 demo3      (offline synthetic data)""")
    p.add_argument("symbols", nargs="+", help="forex pairs (EURUSD, GBP/JPY, XAUUSD) or watchlists (forex, majors, "
                   "minors, metals); other tickers (AAPL, ^GSPC, BTC-USD, BTCUSDT); CSV file paths; or demoN")
    p.add_argument("-i", "--interval", default="1d", help="1m 5m 15m 30m 1h 4h 1d 1wk (default 1d)")
    p.add_argument("--source", default="auto", choices=["auto", "yahoo", "binance", "csv", "demo"])
    p.add_argument("--min-score", type=int, default=0, help="hide setups scoring below this (0-100)")
    p.add_argument("--top", type=int, default=3, help="detailed setups shown per symbol (default 3)")
    p.add_argument("--account", type=float, help="account size, for position sizing")
    p.add_argument("--risk", type=float, default=1.0, help="%% of account risked per trade (default 1)")
    p.add_argument("--ccy", default="USD", help="account currency for forex lot sizing (default USD)")
    p.add_argument("--swing", type=int, default=3, help="bars each side defining a swing point (default 3)")
    p.add_argument("--json", metavar="FILE", help="also write the full analysis as JSON")
    p.add_argument("--chart", metavar="DIR", help="write an interactive HTML chart per symbol into DIR")
    p.add_argument("--no-color", action="store_true")
    a = p.parse_args(argv)
    report.USE_COLOR = not a.no_color and sys.stdout.isatty()

    results, all_setups = [], []
    ccy = a.ccy.upper()
    rates = {}

    def rate(q, acc):
        if (q, acc) not in rates:
            rates[(q, acc)] = fx_rate(q, acc)
        return rates[(q, acc)]

    for sym in expand(a.symbols):
        try:
            df = load(sym, a.interval, a.source)
        except Exception as e:  # network / ticker / file problems: report and continue with the rest
            print(report.c(f"\n ✗ {sym}: could not load data - {e}", "31"))
            continue
        fx = None if sym.lower().endswith(".csv") or a.source not in ("auto", "yahoo") else parse(sym)
        if fx:
            name = fx["pair"]
        elif sym.lower().endswith(".csv"):
            name = os.path.splitext(os.path.basename(sym))[0]
        else:
            name = sym.upper()
        setups.PRICE_DP = decimals(fx) if fx else None
        ctx = analyze(df, name, a.interval, a.swing)
        ctx["fx"] = fx
        q2acc = None
        if fx and a.account:
            try:
                from .forex import quote_to_account
                q2acc = quote_to_account(fx, ctx["px"], ccy, rate)
            except Exception as e:
                print(report.c(f"   (could not get {fx['quote']}{ccy} rate for lot sizing: {e})", "33"))
        for s_ in ctx["setups"]:
            if fx:
                s_.risk_label = f"{s_.risk / fx['pip']:.1f}p"
            if fx and fx["metal"]:
                s_.warnings.append(f"Prices are COMEX {fx['metal']} futures ({fx['yahoo']}) - spot quotes at your broker "
                                   "differ slightly, so shift levels by the spread between them")
        ctx["setups"] = [s for s in ctx["setups"] if s.score >= a.min_score]
        results.append(ctx)
        all_setups += ctx["setups"]

        print(report.market_overview(ctx))
        print(report.c(f"\n Trade setups found: {len(ctx['setups'])}", "1"))
        if not ctx["setups"]:
            print("   No setup meets the rules right now - the best trade may be no trade. Watch the key levels above.")
        for k, s in enumerate(ctx["setups"][:a.top], 1):
            print(report.setup_block(s, k, a.account, a.risk if a.account else None, fx, q2acc, ccy))
        if a.chart:
            os.makedirs(a.chart, exist_ok=True)
            path = os.path.join(a.chart, f"{name.replace('/', '_').replace('^', '')}_{a.interval}.html")
            report.write_chart(ctx, path)
            print(f"\n   Chart saved: {path}")
        setups.PRICE_DP = None

    if not results:
        return 1
    all_setups.sort(key=lambda s: -s.score)
    if all_setups:
        print(report.ranking_table(all_setups))
        best = all_setups[0]
        pos, _ = best.summary()
        print(report.c(f"\n Best opportunity: {best.side} {best.symbol} ({best.strategy}, score {best.score}, "
                       f"grade {best.grade}).", "1"))
        if pos:
            print("   Mainly because: " + "; ".join(pos[:3]) + ".")
    if a.json:
        report.write_json(results, a.json)
        print(f"\n JSON written to {a.json}")
    print(report.c("\n " + DISCLAIMER, "2"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
