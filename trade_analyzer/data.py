"""Market data loading: CSV files, Yahoo Finance (stocks, FX, indices, futures, crypto) and Binance (crypto)."""
import numpy as np
import pandas as pd
import requests

UA = {"User-Agent": "Mozilla/5.0 (trade-analyzer)"}

YAHOO_RANGE = {"1m": "5d", "5m": "60d", "15m": "60d", "30m": "60d", "1h": "730d", "4h": "730d",
               "1d": "2y", "1wk": "10y", "1mo": "max"}
BINANCE_IV = {"1m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "12h", "1d", "1w", "1M"}
CRYPTO_QUOTES = ("USDT", "USDC", "FDUSD", "BUSD")


def _finish(df: pd.DataFrame) -> pd.DataFrame:
    df = df[["open", "high", "low", "close", "volume"]].astype(float)
    df = df.dropna(subset=["open", "high", "low", "close"]).sort_index()
    df = df[~df.index.duplicated(keep="last")]
    df["volume"] = df["volume"].fillna(0.0)
    if len(df) < 60:
        raise ValueError(f"Only {len(df)} candles available - need at least 60 for a reliable analysis.")
    return df


def load_csv(path: str) -> pd.DataFrame:
    """CSV with columns date/time/timestamp, open, high, low, close[, volume] (any case, e.g. a TradingView export)."""
    df = pd.read_csv(path)
    df.columns = [str(c).strip().lower() for c in df.columns]
    date_col = next((c for c in df.columns if c in ("date", "datetime", "time", "timestamp", "date/time")), None)
    if date_col is None:
        raise ValueError("CSV needs a date/time column (date, datetime, time or timestamp).")
    col = df[date_col]
    if pd.api.types.is_numeric_dtype(col):  # unix seconds or milliseconds
        col = pd.to_datetime(col, unit="ms" if col.max() > 1e11 else "s", utc=True)
    else:
        col = pd.to_datetime(col, utc=True, format="mixed")
    df.index = col
    alias = {"adj close": None, "vol": "volume", "volume usdt": "volume"}
    for k, v in alias.items():
        if k in df.columns and v and v not in df.columns:
            df[v] = df[k]
    missing = [c for c in ("open", "high", "low", "close") if c not in df.columns]
    if missing:
        raise ValueError(f"CSV is missing columns: {', '.join(missing)}")
    if "volume" not in df.columns:
        df["volume"] = 0.0
    return _finish(df)


def fetch_yahoo(symbol: str, interval: str = "1d") -> pd.DataFrame:
    yi = "1h" if interval == "4h" else interval
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
    r = requests.get(url, params={"interval": "60m" if yi == "1h" else yi, "range": YAHOO_RANGE.get(interval, "2y")},
                     headers=UA, timeout=20)
    r.raise_for_status()
    res = (r.json().get("chart") or {}).get("result")
    if not res:
        raise ValueError(f"Yahoo Finance returned no data for '{symbol}'. Check the ticker (e.g. AAPL, EURUSD=X, BTC-USD, ^GSPC, GC=F).")
    res = res[0]
    q = res["indicators"]["quote"][0]
    df = pd.DataFrame({k: q.get(k) for k in ("open", "high", "low", "close", "volume")},
                      index=pd.to_datetime(res["timestamp"], unit="s", utc=True))
    if interval == "4h":
        df = df.resample("4h").agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
    return _finish(df)


def fetch_binance(symbol: str, interval: str = "1d", limit: int = 1000) -> pd.DataFrame:
    iv = {"1wk": "1w", "1mo": "1M"}.get(interval, interval)
    if iv not in BINANCE_IV:
        raise ValueError(f"Binance does not support interval '{interval}'.")
    r = requests.get("https://api.binance.com/api/v3/klines",
                     params={"symbol": symbol.upper(), "interval": iv, "limit": limit}, headers=UA, timeout=20)
    r.raise_for_status()
    rows = r.json()
    df = pd.DataFrame(rows, columns=["t", "open", "high", "low", "close", "volume", *range(6)])
    df.index = pd.to_datetime(df["t"], unit="ms", utc=True)
    return _finish(df)


def synthetic(n: int = 500, seed: int = 7, start: float = 100.0) -> pd.DataFrame:
    """Random-walk OHLCV with trending regimes - for demos and tests (no internet needed)."""
    rng = np.random.default_rng(seed)
    drift = np.repeat(rng.normal(0, 0.004, n // 60 + 1), 60)[:n]
    ret = drift + rng.normal(0, 0.015, n)
    close = start * np.exp(np.cumsum(ret))
    open_ = np.r_[start, close[:-1]] * (1 + rng.normal(0, 0.003, n))
    span = np.abs(rng.normal(0, 0.01, n)) * close
    high = np.maximum(open_, close) + span
    low = np.minimum(open_, close) - span
    vol = rng.lognormal(13, 0.4, n) * (1 + 3 * np.abs(ret))
    idx = pd.date_range(end=pd.Timestamp.now(tz="UTC").normalize(), periods=n, freq="D")
    return _finish(pd.DataFrame({"open": open_, "high": high, "low": low, "close": close, "volume": vol}, index=idx))


def load(symbol: str, interval: str = "1d", source: str = "auto") -> pd.DataFrame:
    if source == "csv" or symbol.lower().endswith(".csv"):
        return load_csv(symbol)
    if source == "demo" or symbol.lower().startswith("demo"):
        seed = int("".join(ch for ch in symbol if ch.isdigit()) or 7)
        return synthetic(seed=seed)
    if source == "binance" or (source == "auto" and "-" not in symbol and symbol.upper().endswith(CRYPTO_QUOTES)):
        try:
            return fetch_binance(symbol, interval)
        except requests.RequestException:
            if source == "binance":
                raise
            # Binance unreachable or geo-blocked (HTTP 451): fall back to the same coin in USD on Yahoo
            base = symbol.upper()
            for q in CRYPTO_QUOTES:
                if base.endswith(q):
                    base = base[: -len(q)]
                    break
            return fetch_yahoo(f"{base}-USD", interval)
    from .forex import parse
    fx = parse(symbol) if source in ("auto", "yahoo") else None
    return fetch_yahoo(fx["yahoo"] if fx else symbol, interval)


def fx_rate(base: str, quote: str) -> float:
    """Latest Yahoo close for base/quote (used to convert pip values into the account currency)."""
    r = requests.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{base}{quote}=X",
                     params={"interval": "1d", "range": "5d"}, headers=UA, timeout=20)
    r.raise_for_status()
    closes = [c for c in r.json()["chart"]["result"][0]["indicators"]["quote"][0]["close"] if c]
    return float(closes[-1])
