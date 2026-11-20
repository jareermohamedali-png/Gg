# Trade Analyzer

A command-line technical analysis tool. Give it one or more tickers (or a CSV export) and it:

| Step | What it does |
|---|---|
| **Analyse the chart** | Trend (EMA 20/50/200 stack + ADX), market structure (swing HH/HL/LH/LL, break of structure / change of character), support & resistance (clustered swing points, scored by touches and recency), indicators (RSI, MACD, ADX/DI, ATR, Bollinger, Stochastic, volume), RSI divergence and confirmation candles. |
| **Find opportunities** | Four BUY and SELL setup types: **Trend pullback**, **Breakout** (market or retest limit), **S/R bounce** and **Momentum** (MACD flip). |
| **Calculate the entry** | Market entry at the last close, or a limit order at the retest of a broken level when price is already extended. |
| **Set the stop loss** | Beyond the nearest structural point that proves the idea wrong (swing low/high or tested level) with a 0.3 ATR buffer; ATR fallback; never tighter than 0.8 ATR. |
| **Set TP1 / TP2 / TP3** | Placed just in front of the next tested levels in the trade direction, falling back to 1.5R / 2.5R / 4R projections. R:R shown for each. |
| **Compare opportunities** | Each setup gets a 0–100 confluence score and an A–D grade; all setups across all symbols are ranked in one table. |
| **Explain the decision** | Every point added or removed is listed in plain language ("Why this trade" / "Risks"), plus the exact invalidation condition. |

Optional: position sizing (`--account`), JSON export (`--json`) and an interactive HTML chart with levels, entry, SL and TPs drawn on it (`--chart`).

## Install

Python 3.9+:

```bash
pip install -r trade_analyzer/requirements.txt
```

## Forex

Built for forex first. Type pairs the way you see them on your platform, or scan a whole watchlist:

```bash
python -m trade_analyzer forex                         # majors + minors + gold + silver (23 instruments)
python -m trade_analyzer majors -i 4h                  # 7 majors on the 4-hour chart
python -m trade_analyzer minors -i 1h --min-score 60   # crosses, only B grade or better
python -m trade_analyzer EURUSD GBP/JPY XAUUSD -i 4h --account 5000 --risk 1
python -m trade_analyzer EURUSD --account 4000 --ccy EUR   # account in euros
```

For forex pairs the report shows:
- prices in broker precision (5 decimals, 3 for JPY pairs, 2 for gold)
- ATR, stop distance and each TP in **pips**
- with `--account`: **lot size** (standard / mini / micro) and pip value per lot in your account
  currency (`--ccy`, default USD), so hitting the stop costs exactly `--risk` %

Watchlists: `majors`, `minors`, `metals`, `forex` (all of them). Data comes from Yahoo Finance and
has no real volume for spot FX, so volume is ignored for pairs. `XAUUSD`/`XAGUSD` use COMEX gold/silver
futures as a stand-in for spot (prices differ slightly from your broker; gold "pips" are 0.10).
Intervals: `15m` and `30m` (last 60 days), `1h` and `4h` (2 years), `1d`, `1wk`.

## Use (other markets)

Run from the folder that **contains** `trade_analyzer/`:

```bash
# Stocks, forex, indices, commodities, crypto via Yahoo Finance
python -m trade_analyzer AAPL MSFT NVDA EURUSD=X ^GSPC GC=F BTC-USD

# Crypto via Binance (symbols ending in USDT/USDC), 4-hour candles, size for a 10k account risking 1%
python -m trade_analyzer BTCUSDT ETHUSDT SOLUSDT -i 4h --account 10000 --risk 1

# Your own data (e.g. TradingView "Export chart data"), with an HTML chart
python -m trade_analyzer my_chart.csv --chart charts/

# Only show decent setups, save everything as JSON
python -m trade_analyzer AAPL TSLA AMD --min-score 60 --json analysis.json

# Offline demo with synthetic data
python -m trade_analyzer demo1 demo2 demo3
```

Intervals: `1m 5m 15m 30m 1h 4h 1d 1wk` (default `1d`). CSV files need a date/time column and
`open, high, low, close` (volume optional; column names are case-insensitive).

Other options: `--top N` setups shown per symbol, `--swing N` bars each side for a swing point
(higher = only bigger swings), `--source yahoo|binance|csv|demo`, `--no-color`.

## Example output (shortened)

```
 Trend      Uptrend (strong)
 Structure  bullish  ·  recent swings: LL → HH → HL → HL
 ...
 #1  BUY sample - Trend pullback   score 70/100 ██████████████░░░░░░  grade B · Tradeable setup
     Trigger : price pulled back to the EMA20 inside an uptrend
     Entry   : 186.38  (market)
     Stop    : 179.57  risk 6.8077 (3.65%)  ← tested support 181.04 (7 touches) minus 0.3 ATR buffer
     TP1     : 194.83  R:R 1:1.24  ← just before resistance 195.32 (5 touches)
     TP2     : 205.73  R:R 1:2.84  ← just before resistance 206.22 (2 touches)
     TP3     : 213.61  R:R 1:4.00  ← 4.0R measured projection
     Why this trade:
       + Price and EMAs fully stacked in trade direction (EMA20/50/200 aligned)
       + Market structure is bullish (higher highs & higher lows)
       + ADX 56 - strong trend in trade direction
       + Volume 1.6x the 20-bar average - strong participation
     Risks / against:
       - Tested resistance 189.77 is only 0.5R away - little room before TP1
     Invalidation: A close below 179.57 (...) invalidates the idea.
```

## How the score works

Each setup starts from a base (30–35) and gains or loses points for confluence:
EMA trend alignment (±12), market structure (±10), recent BOS/CHoCH (±6), ADX trend strength (±6),
RSI zone and RSI divergence (±8), MACD momentum (±6), volume (up to +8, −6 for low-volume breakouts),
confirmation candle (±6), stop protected by a tested level (+5), reward/risk to TP2 (+8 to −5) and a
tested level blocking the path before 1R (−8). Grades: **A** ≥ 75, **B** ≥ 60, **C** ≥ 45, **D** below.

The rules live in `setups.py` and are easy to tune.

## Files

- `data.py` – loading from Yahoo Finance, Binance, CSV or synthetic demo data
- `indicators.py` – EMA, RSI, MACD, ATR, ADX/DI, Bollinger, Stochastic
- `structure.py` – swing points, HH/HL/LH/LL, BOS/CHoCH, support/resistance, divergence, candles
- `setups.py` – setup detection, entry / stop / targets, scoring and explanations
- `analysis.py` – puts it all together for one instrument
- `report.py` – terminal report, JSON export, HTML chart

> **Disclaimer:** educational tool, technical analysis only, not financial advice. No setup is a
> guarantee; markets can gap through stops. Always size positions so a loss is affordable.
