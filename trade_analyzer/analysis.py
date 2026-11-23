"""Full chart analysis for one instrument: indicators, trend, structure, levels and setups."""
import pandas as pd

from .indicators import add_indicators
from .setups import find_setups
from .structure import (break_of_structure, candle_signal, find_swings, rsi_divergence,
                        structure_trend, support_resistance)


def ema_direction(px, e20, e50, e200) -> int:
    if px > e20 > e50 > e200:
        return 2
    if px < e20 < e50 < e200:
        return -2
    if px > e50 and e50 > e200:
        return 1
    if px < e50 and e50 < e200:
        return -1
    return 0


def analyze(df: pd.DataFrame, symbol: str, interval: str = "1d", swing_len: int = 3) -> dict:
    df = add_indicators(df)
    return build_context(df, find_swings(df, swing_len, swing_len), symbol, interval)


def build_context(df: pd.DataFrame, swings: list, symbol: str, interval: str = "1d") -> dict:
    """df must already carry indicators; swings must only contain points confirmed by df's last bar.
    Split out of analyze() so the backtester can reuse precomputed indicators/swings bar by bar."""
    last, prev = df.iloc[-1], df.iloc[-2]
    px, a = float(last["close"]), float(last["atr"])
    ind = {k: float(last[k]) for k in ("ema20", "ema50", "ema200", "rsi", "macd", "macd_signal", "macd_hist",
                                        "atr", "adx", "plus_di", "minus_di", "bb_up", "bb_mid", "bb_low",
                                        "stoch_k", "stoch_d")}
    struct = structure_trend(swings)
    e_dir = ema_direction(px, ind["ema20"], ind["ema50"], ind["ema200"])
    prior = struct["trend"] if struct["trend"] in ("bullish", "bearish") else \
        ("bullish" if e_dir > 0 else "bearish" if e_dir < 0 else "none")
    levels = support_resistance(df, swings, a)
    cdir, ctxt = candle_signal(df)
    vol_ok = df["volume"].iloc[-20:].sum() > 0
    ema50 = df["ema50"].to_numpy()
    ctx = {
        "symbol": symbol, "interval": interval, "df": df, "bars": len(df), "time": df.index[-1],
        "px": px, "atr": a, "high": float(last["high"]), "low": float(last["low"]), "ind": ind,
        "rsi_prev": float(prev["rsi"]), "macd_hist_prev": float(prev["macd_hist"]),
        "ema_dir": e_dir, "ema50_slope": (ema50[-1] - ema50[-11]) / a if len(ema50) > 11 else 0.0,
        "swings": swings, "structure": struct, "bos": break_of_structure(df, swings, prior),
        "levels": levels, "divergence": rsi_divergence(df, swings), "candle_dir": cdir, "candle_txt": ctxt,
        "vol_ratio": float(last["volume"] / last["vol_avg"]) if vol_ok and last["vol_avg"] > 0 else None,
        "change_pct": 100 * (px / float(prev["close"]) - 1),
    }
    ctx["trend_label"] = trend_label(ctx)
    ctx["setups"] = find_setups(ctx)
    return ctx


def trend_label(ctx) -> str:
    e, st, adx = ctx["ema_dir"], ctx["structure"]["trend"], ctx["ind"]["adx"]
    strength = "strong" if adx >= 25 else "moderate" if adx >= 18 else "weak"
    if e > 0 and st != "bearish":
        return f"Uptrend ({strength})"
    if e < 0 and st != "bullish":
        return f"Downtrend ({strength})"
    if (e > 0 and st == "bearish") or (e < 0 and st == "bullish"):
        return "Transition / possible reversal"
    return f"Sideways / range ({strength} ADX)"
