"""Market structure: swing points, HH/HL/LH/LL, trend, break of structure, support/resistance, divergence."""
from dataclasses import dataclass, field

import pandas as pd


@dataclass
class Swing:
    i: int          # bar position
    price: float
    kind: str       # "high" | "low"
    label: str = "" # HH / LH / HL / LL


@dataclass
class Level:
    price: float
    touches: int
    last_i: int
    kinds: set = field(default_factory=set)
    strength: float = 0.0
    side: str = ""   # "support" / "resistance" relative to the current price

    @property
    def role(self) -> str:
        return self.side + (" (flip)" if len(self.kinds) == 2 else "")


def find_swings(df: pd.DataFrame, left: int = 3, right: int = 3) -> list:
    """Fractal pivots: a bar whose high (low) is the extreme of `left` bars before and `right` bars after."""
    h, l = df["high"].to_numpy(), df["low"].to_numpy()
    out = []
    for i in range(left, len(df) - right):
        if h[i] == h[i - left:i + right + 1].max() and h[i] > h[i - left:i].max():
            out.append(Swing(i, float(h[i]), "high"))
        if l[i] == l[i - left:i + right + 1].min() and l[i] < l[i - left:i].min():
            out.append(Swing(i, float(l[i]), "low"))
    last = {"high": None, "low": None}
    for s in out:
        prev = last[s.kind]
        if prev is not None:
            if s.kind == "high":
                s.label = "HH" if s.price > prev.price else "LH"
            else:
                s.label = "HL" if s.price > prev.price else "LL"
        last[s.kind] = s
    return out


def structure_trend(swings: list) -> dict:
    highs = [s for s in swings if s.kind == "high"][-2:]
    lows = [s for s in swings if s.kind == "low"][-2:]
    if len(highs) < 2 or len(lows) < 2:
        return {"trend": "unclear", "sequence": "", "last_high": highs[-1] if highs else None,
                "last_low": lows[-1] if lows else None}
    hh, hl = highs[1].price > highs[0].price, lows[1].price > lows[0].price
    trend = "bullish" if hh and hl else "bearish" if not hh and not hl else "ranging"
    seq = " → ".join(s.label for s in swings[-6:] if s.label)
    return {"trend": trend, "sequence": seq, "last_high": highs[-1], "last_low": lows[-1]}


def break_of_structure(df: pd.DataFrame, swings: list, prior_trend: str, lookback: int = 5):
    """Has a recent close broken the last confirmed swing high/low? BOS = with trend, CHoCH = against it."""
    close = df["close"].to_numpy()
    n = len(close)
    for kind, sign in (("high", 1), ("low", -1)):
        pts = [s for s in swings if s.kind == kind and s.i < n - 1]
        if not pts:
            continue
        lvl = pts[-1]
        for j in range(max(lvl.i + 1, n - lookback), n):
            if sign * (close[j] - lvl.price) > 0 and sign * (close[j - 1] - lvl.price) <= 0:
                direction = "bullish" if sign > 0 else "bearish"
                with_trend = (prior_trend == direction)
                return {"direction": direction, "type": "BOS" if with_trend else "CHoCH",
                        "level": lvl.price, "bars_ago": n - 1 - j}
    return None


def support_resistance(df: pd.DataFrame, swings: list, atr_now: float, recent: int = 6, lookback: int = 500) -> list:
    """Cluster swing prices that sit within ~0.6 ATR of each other into levels; more touches + recency = stronger.
    Keeps every level touched at least twice, plus the most recent single-touch swings (fresh highs/lows)."""
    tol = 0.6 * atr_now
    levels = []
    start = len(df) - lookback          # only the last `lookback` bars matter for current levels
    for s in sorted((s for s in swings if s.i >= start), key=lambda s: s.price):
        if levels and abs(s.price - levels[-1].price) <= tol:
            L = levels[-1]
            L.price = (L.price * L.touches + s.price) / (L.touches + 1)
            L.touches += 1
            L.last_i = max(L.last_i, s.i)
            L.kinds.add(s.kind)
        else:
            levels.append(Level(s.price, 1, s.i, {s.kind}))
    n = len(df)
    for L in levels:
        recency = 1 - (n - L.last_i) / n          # 0..1
        L.strength = round(L.touches + 1.5 * recency + (0.5 if len(L.kinds) == 2 else 0), 2)
    px = float(df["close"].iloc[-1])
    fresh = {s.i for s in swings[-recent:]}
    keep = []
    for L in levels:
        if L.touches >= 2 or L.last_i in fresh:
            L.side = "support" if L.price <= px else "resistance"
            keep.append(L)
    return keep


def rsi_divergence(df: pd.DataFrame, swings: list, max_age: int = 15):
    """Regular divergence on the two latest swing lows (bullish) or highs (bearish)."""
    n, r = len(df), df["rsi"].to_numpy()
    lows = [s for s in swings if s.kind == "low"][-2:]
    highs = [s for s in swings if s.kind == "high"][-2:]
    if len(lows) == 2 and n - 1 - lows[1].i <= max_age and lows[1].price < lows[0].price and r[lows[1].i] > r[lows[0].i] + 2:
        return "bullish"
    if len(highs) == 2 and n - 1 - highs[1].i <= max_age and highs[1].price > highs[0].price and r[highs[1].i] < r[highs[0].i] - 2:
        return "bearish"
    return None


def candle_signal(df: pd.DataFrame):
    """Simple confirmation candles on the last bar: engulfing, hammer / shooting star, strong close."""
    o, h, l, c = (df[k].to_numpy() for k in ("open", "high", "low", "close"))
    po, pc = o[-2], c[-2]
    body, rng = abs(c[-1] - o[-1]), max(h[-1] - l[-1], 1e-12)
    lower_wick, upper_wick = min(o[-1], c[-1]) - l[-1], h[-1] - max(o[-1], c[-1])
    if c[-1] > o[-1] and pc < po and c[-1] >= po and o[-1] <= pc:
        return "bullish", "bullish engulfing candle"
    if c[-1] < o[-1] and pc > po and c[-1] <= po and o[-1] >= pc:
        return "bearish", "bearish engulfing candle"
    if lower_wick > 2 * body and upper_wick < body + 0.1 * rng:
        return "bullish", "hammer / long lower wick (buyers rejected lower prices)"
    if upper_wick > 2 * body and lower_wick < body + 0.1 * rng:
        return "bearish", "shooting star / long upper wick (sellers rejected higher prices)"
    if c[-1] > o[-1] and (c[-1] - l[-1]) / rng > 0.8 and body / rng > 0.6:
        return "bullish", "strong bullish close near the high"
    if c[-1] < o[-1] and (h[-1] - c[-1]) / rng > 0.8 and body / rng > 0.6:
        return "bearish", "strong bearish close near the low"
    return None, None
