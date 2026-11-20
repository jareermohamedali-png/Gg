"""Technical indicators (pure pandas / numpy, no TA library needed)."""
import numpy as np
import pandas as pd


def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False).mean()


def sma(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n, min_periods=1).mean()


def wilder(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(alpha=1 / n, adjust=False).mean()


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    d = close.diff()
    gain = wilder(d.clip(lower=0), n)
    loss = wilder(-d.clip(upper=0), n)
    out = 100 - 100 / (1 + gain / loss.replace(0, np.nan))
    out = out.where(loss != 0, 100.0)          # no losses -> RSI 100
    return out.where(gain + loss != 0, 50.0)   # flat -> neutral


def true_range(df: pd.DataFrame) -> pd.Series:
    pc = df["close"].shift()
    return pd.concat([df["high"] - df["low"], (df["high"] - pc).abs(), (df["low"] - pc).abs()], axis=1).max(axis=1)


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    return wilder(true_range(df), n)


def macd(close: pd.Series, fast=12, slow=26, signal=9):
    line = ema(close, fast) - ema(close, slow)
    sig = ema(line, signal)
    return line, sig, line - sig


def adx(df: pd.DataFrame, n: int = 14):
    up = df["high"].diff()
    down = -df["low"].diff()
    plus_dm = pd.Series(np.where((up > down) & (up > 0), up, 0.0), index=df.index)
    minus_dm = pd.Series(np.where((down > up) & (down > 0), down, 0.0), index=df.index)
    tr = wilder(true_range(df), n)
    plus_di = 100 * wilder(plus_dm, n) / tr
    minus_di = 100 * wilder(minus_dm, n) / tr
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return wilder(dx.fillna(0), n), plus_di, minus_di


def bollinger(close: pd.Series, n=20, k=2.0):
    mid = sma(close, n)
    sd = close.rolling(n, min_periods=1).std(ddof=0)
    return mid + k * sd, mid, mid - k * sd


def stochastic(df: pd.DataFrame, n=14, d=3):
    lo = df["low"].rolling(n, min_periods=1).min()
    hi = df["high"].rolling(n, min_periods=1).max()
    k = 100 * (df["close"] - lo) / (hi - lo).replace(0, np.nan)
    k = k.fillna(50)
    return k, k.rolling(d, min_periods=1).mean()


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    c = df["close"]
    for n in (20, 50, 200):
        df[f"ema{n}"] = ema(c, n)
    df["rsi"] = rsi(c)
    df["macd"], df["macd_signal"], df["macd_hist"] = macd(c)
    df["atr"] = atr(df)
    df["adx"], df["plus_di"], df["minus_di"] = adx(df)
    df["bb_up"], df["bb_mid"], df["bb_low"] = bollinger(c)
    df["stoch_k"], df["stoch_d"] = stochastic(df)
    df["vol_avg"] = sma(df["volume"], 20)
    return df
