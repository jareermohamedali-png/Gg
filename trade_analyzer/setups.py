"""Trade setup detection, entries / stops / targets, confluence scoring and explanations."""
from dataclasses import dataclass, field
from typing import Optional

from .structure import Level

BUY, SELL = 1, -1
NAME = {BUY: "BUY", SELL: "SELL"}
TREND_STRATS = {"Trend pullback", "Breakout", "Momentum"}


@dataclass
class Target:
    price: float
    rr: float
    basis: str


@dataclass
class Setup:
    symbol: str
    direction: int
    strategy: str
    trigger: str
    entry: float
    entry_type: str            # "market" or "limit"
    stop: float
    stop_basis: str
    targets: list = field(default_factory=list)
    score: int = 0
    reasons: list = field(default_factory=list)   # (points, text)
    warnings: list = field(default_factory=list)

    @property
    def side(self): return NAME[self.direction]

    @property
    def risk(self): return abs(self.entry - self.stop)

    @property
    def risk_pct(self): return 100 * self.risk / self.entry

    @property
    def grade(self):
        return "A" if self.score >= 75 else "B" if self.score >= 60 else "C" if self.score >= 45 else "D"

    @property
    def verdict(self):
        return {"A": "High-quality setup", "B": "Tradeable setup", "C": "Marginal - wait for extra confirmation",
                "D": "Weak - watchlist only"}[self.grade]

    @property
    def invalidation(self):
        word = "below" if self.direction == BUY else "above"
        return f"A close {word} {fmt(self.stop)} ({self.stop_basis}) invalidates the idea."

    def summary(self):
        pos = [t for p, t in sorted(self.reasons, key=lambda r: -r[0]) if p > 0][:4]
        neg = [t for p, t in sorted(self.reasons, key=lambda r: r[0]) if p < 0][:3] + self.warnings
        return pos, neg


def fmt(x: float) -> str:
    a = abs(x)
    d = 2 if a >= 100 else 3 if a >= 10 else 4 if a >= 1 else 6
    return f"{x:,.{d}f}"


# ----------------------------------------------------------------------------------------------- building blocks

def _stop_from_structure(ctx, d, entry, anchors):
    """Stop beyond the nearest structural anchor (swing point or tested level) that is 0.7-4 ATR away,
    plus a 0.3 ATR buffer. Falls back to a 1.5 ATR volatility stop; never tighter than 0.8 ATR."""
    a = ctx["atr"]
    ok = [(p, t) for p, t in anchors if p is not None and 0.5 * a <= d * (entry - p) <= 4 * a]
    if ok:
        p, t = min(ok, key=lambda x: abs(entry - x[0]))
        stop = p - d * 0.3 * a
        basis = f"{t.format(p=fmt(p))} {'minus' if d == BUY else 'plus'} 0.3 ATR buffer"
    else:
        stop, basis = entry - d * 1.5 * a, "1.5 ATR volatility stop (no nearby structure)"
    if abs(entry - stop) < 0.8 * a:
        stop, basis = entry - d * 0.8 * a, basis + ", widened to the 0.8 ATR minimum"
    return stop, basis


def _anchors(ctx, d):
    """Structural places where a long (short) idea is proven wrong: last swing low (high) and tested levels."""
    sw = ctx["structure"]["last_low" if d == BUY else "last_high"]
    out = [(sw.price if sw else None, "last swing low {p}" if d == BUY else "last swing high {p}")]
    out += [(L.price, f"tested {'support' if d == BUY else 'resistance'} {{p}} ({L.touches} touches)")
            for L in ctx["levels"] if L.touches >= 2]
    return out


def _targets(ctx, d, entry, stop):
    a, R = ctx["atr"], abs(entry - stop)
    ahead = sorted((L for L in ctx["levels"] if d * (L.price - entry) > 0.3 * R and L.touches >= 2), key=lambda L: d * (L.price - entry))
    cands = [(L.price - d * 0.1 * a, L) for L in ahead]          # exit just in front of the level
    needs, fallback, tps = (1.0, 2.0, 3.0), (1.5, 2.5, 4.0), []
    for need, fb in zip(needs, fallback):
        prev = tps[-1].rr if tps else 0.0
        hit = next(((p, L) for p, L in cands if d * (p - entry) / R >= max(need, prev + 0.4)), None)
        if hit and d * (hit[0] - entry) / R <= 8:
            p, L = hit
            tps.append(Target(p, round(d * (p - entry) / R, 2), f"just before {L.role} {fmt(L.price)} ({L.touches} touches)"))
        else:
            rr = max(fb, round(prev + 0.5, 1))
            tps.append(Target(entry + d * rr * R, rr, f"{rr}R measured projection"))
    first_wall = next((L for L in ahead if L.touches >= 2), None)
    return tps, first_wall


def _make(ctx, d, strategy, trigger, entry, entry_type, fixed_stop=None):
    stop, basis = fixed_stop or _stop_from_structure(ctx, d, entry, _anchors(ctx, d))
    s = Setup(ctx["symbol"], d, strategy, trigger, entry, entry_type, stop, basis)
    s.targets, wall = _targets(ctx, d, entry, stop)
    _score(s, ctx, wall)
    return s


def _near(levels, px, a, d, max_dist=0.6, min_touches=2) -> Optional[Level]:
    """Nearest tested level on the 'protective' side: support for buys (d=1), resistance for sells."""
    side = [L for L in levels if L.touches >= min_touches and -d * (L.price - px) >= -0.25 * a
            and abs(L.price - px) <= max_dist * a]
    return min(side, key=lambda L: abs(L.price - px)) if side else None


# ----------------------------------------------------------------------------------------------- strategies

def trend_pullback(ctx, d):
    px, a, ind = ctx["px"], ctx["atr"], ctx["ind"]
    in_trend = ctx["ema_dir"] * d >= 1 or (ctx["structure"]["trend"] == ("bullish" if d == BUY else "bearish")
                                            and ctx["ema50_slope"] * d > 0)
    if not in_trend:
        return None
    touched = [n for n in ("ema20", "ema50") if abs(px - ind[n]) <= a or
               (d == BUY and ctx["low"] <= ind[n] <= px) or (d == SELL and ctx["high"] >= ind[n] >= px)]
    lo, hi = (35, 62) if d == BUY else (38, 65)
    if not touched or not lo <= ind["rsi"] <= hi:
        return None
    return _make(ctx, d, "Trend pullback", f"price pulled back to the {touched[0].upper()} inside a"
                 f"{'n up' if d == BUY else ' down'}trend", px, "market")


def breakout(ctx, d):
    px, a, df = ctx["px"], ctx["atr"], ctx["df"]
    closes = df["close"].to_numpy()
    lvl, txt = None, ""
    bos = ctx["bos"]
    if bos and bos["direction"] == ("bullish" if d == BUY else "bearish") and bos["bars_ago"] <= 3:
        lvl, txt = bos["level"], f"{bos['type']}: close broke the last swing {'high' if d == BUY else 'low'}"
    else:
        for L in ctx["levels"]:
            if L.touches >= 2 and d * (px - L.price) > 0 and min(d * (closes[-4:-1] - L.price)) <= 0:
                lvl, txt = L.price, f"close broke {'above' if d == BUY else 'below'} {L.role} ({L.touches} touches)"
                break
    if lvl is None or d * (px - lvl) > 2.5 * a:
        return None
    extended = d * (px - lvl) > 0.6 * a
    entry = lvl + d * 0.15 * a if extended else px
    stop = (lvl - d * 0.8 * a, f"0.8 ATR {'below' if d == BUY else 'above'} the broken level {fmt(lvl)}")
    s = _make(ctx, d, "Breakout", txt + (" - wait for a retest" if extended else ""), entry,
              "limit (retest)" if extended else "market", fixed_stop=stop)
    return s


def sr_bounce(ctx, d):
    px, a, ind = ctx["px"], ctx["atr"], ctx["ind"]
    L = _near(ctx["levels"], px, a, d) or _near(ctx["levels"], ctx["low"] if d == BUY else ctx["high"], a, d, 0.3)
    if L is None:
        return None
    osc = (ind["rsi"] < 45 or ind["stoch_k"] < 25) if d == BUY else (ind["rsi"] > 55 or ind["stoch_k"] > 75)
    div = ctx["divergence"] == ("bullish" if d == BUY else "bearish")
    candle = ctx["candle_dir"] == ("bullish" if d == BUY else "bearish")
    if not (osc or div or candle):
        return None
    stop = (L.price - d * 0.6 * a, f"0.6 ATR {'below support' if d == BUY else 'above resistance'} {fmt(L.price)}")
    if d * (stop[0] - (ctx["low"] if d == BUY else ctx["high"])) > 0:   # keep the stop beyond the current bar's wick
        w = ctx["low"] if d == BUY else ctx["high"]
        stop = (w - d * 0.3 * a, f"beyond this bar's {'low' if d == BUY else 'high'} at the level {fmt(L.price)}")
    return _make(ctx, d, "S/R bounce", f"price testing {L.role} {fmt(L.price)} ({L.touches} touches)", px,
                 "market", fixed_stop=stop)


def momentum(ctx, d):
    h = ctx["df"]["macd_hist"].to_numpy()
    crossed = any(d * h[-k] > 0 and d * h[-k - 1] <= 0 for k in range(1, 4))
    if not crossed or d * (ctx["px"] - ctx["ind"]["ema50"]) <= 0:
        return None
    return _make(ctx, d, "Momentum", f"MACD histogram flipped {'positive' if d == BUY else 'negative'} with price "
                 f"{'above' if d == BUY else 'below'} the EMA50", ctx["px"], "market")


STRATEGIES = (trend_pullback, breakout, sr_bounce, momentum)


def find_setups(ctx) -> list:
    out = []
    for strat in STRATEGIES:
        for d in (BUY, SELL):
            s = strat(ctx, d)
            if s:
                out.append(s)
    return sorted(out, key=lambda s: -s.score)


# ----------------------------------------------------------------------------------------------- scoring

BASE = {"Trend pullback": 35, "Breakout": 32, "S/R bounce": 30, "Momentum": 30}


def _score(s: Setup, ctx, wall):
    d, ind, R = s.direction, ctx["ind"], s.risk
    bounce = s.strategy == "S/R bounce"
    bull = d == BUY
    add = s.reasons.append

    # 1. EMA trend
    e = ctx["ema_dir"] * d
    txt = {2: "Price and EMAs fully stacked in trade direction (EMA20/50/200 aligned)",
           1: "Price on the right side of EMA50 and EMA50 vs EMA200 aligned",
           -1: "Trading against the EMA trend", -2: "Trading against a fully stacked EMA trend"}
    pts = {2: 12, 1: 7, 0: 0, -1: -7, -2: -12}[e]
    if bounce and pts < 0:
        pts //= 2
    if pts:
        add((pts, txt[e]))

    # 2. Market structure
    st = ctx["structure"]["trend"]
    want = "bullish" if bull else "bearish"
    if st == want:
        add((10, f"Market structure is {st} ({'higher highs & higher lows' if bull else 'lower highs & lower lows'})"))
    elif st == "ranging" and bounce:
        add((4, "Ranging structure favours trading from the edges of the range"))
    elif st in ("bullish", "bearish"):
        add((-5 if bounce else -10, f"Market structure is {st} - opposite to this trade"))

    bos = ctx["bos"]
    if bos and bos["bars_ago"] <= 5:
        if bos["direction"] == want:
            add((6 if bos["type"] == "BOS" else 5, f"Recent {bos['type']} {bos['direction']} through {fmt(bos['level'])}"
                 + (" (possible trend change)" if bos["type"] == "CHoCH" else "")))
        else:
            add((-6, f"Recent {bos['type']} in the opposite direction ({bos['direction']})"))

    # 3. Trend strength (ADX / DI)
    adx, di_ok = ind["adx"], (ind["plus_di"] > ind["minus_di"]) == bull
    if adx >= 25 and di_ok:
        add((6, f"ADX {adx:.0f} - strong trend in trade direction"))
    elif adx >= 25 and not di_ok:
        add((-6, f"ADX {adx:.0f} - strong trend against the trade"))
    elif adx < 18:
        add((3, f"ADX {adx:.0f} - no strong trend, mean reversion works better") if bounce
            else (-4, f"ADX {adx:.0f} - weak trend, follow-through is less likely"))

    # 4. RSI / stochastic
    r, rp = ind["rsi"], ctx["rsi_prev"]
    rising = (r - rp) * d > 0
    if (r > 70 and bull) or (r < 30 and not bull):
        add((-4 if s.strategy == "Breakout" else -8, f"RSI {r:.0f} is {'overbought' if bull else 'oversold'} - late entry risk"))
    elif (r < 32 and bull) or (r > 68 and not bull):
        add((6 if bounce else 2, f"RSI {r:.0f} is {'oversold' if bull else 'overbought'} - stretched move likely to snap back"))
    elif 38 <= r <= 62 and rising and not bounce:
        add((5, f"RSI {r:.0f} reset to mid-range and turning {'up' if bull else 'down'}"))
    if ctx["divergence"] == want:
        add((8, f"{want.capitalize()} RSI divergence on the last swings (momentum disagrees with price)"))
    elif ctx["divergence"]:
        add((-8, f"{ctx['divergence'].capitalize()} RSI divergence works against this trade"))

    # 5. MACD
    hist, hp = ind["macd_hist"], ctx["macd_hist_prev"]
    if hist * d > 0 and (hist - hp) * d > 0:
        add((6, "MACD histogram positive and expanding in trade direction"))
    elif (hist - hp) * d > 0:
        add((3, "MACD histogram turning in trade direction"))
    elif hist * d < 0:
        add((-5, "MACD momentum still pointing the other way"))

    # 6. Volume
    vr = ctx["vol_ratio"]
    if vr is not None:
        if vr >= 1.5:
            add((8 if s.strategy == "Breakout" else 5, f"Volume {vr:.1f}x the 20-bar average - strong participation"))
        elif s.strategy == "Breakout" and vr < 1.0:
            add((-6, f"Breakout on below-average volume ({vr:.1f}x) - fakeout risk"))

    # 7. Candle confirmation
    if ctx["candle_dir"] == want:
        add((6, f"Confirmation: {ctx['candle_txt']}"))
    elif ctx["candle_dir"]:
        add((-6, f"Last candle is a {ctx['candle_txt']} - against the trade"))

    # 8. Stop protected by a level
    lo, hi = sorted((s.stop, s.entry))
    guard = [L for L in ctx["levels"] if lo < L.price < hi and L.touches >= 2]
    if guard:
        add((5, f"Stop sits beyond a tested level ({fmt(guard[0].price)}, {guard[0].touches} touches)"))

    # 9. Reward / risk and room to move
    rr2 = s.targets[1].rr
    if rr2 >= 3:
        add((8, f"Excellent reward/risk: TP2 = {rr2:.1f}R"))
    elif rr2 >= 2:
        add((5, f"Good reward/risk: TP2 = {rr2:.1f}R"))
    else:
        add((-5, f"Limited reward/risk: TP2 only {rr2:.1f}R"))
    if wall is not None:
        wall_rr = abs(wall.price - s.entry) / R
        if wall_rr < 1:
            add((-8, f"Tested {wall.role} {fmt(wall.price)} is only {wall_rr:.1f}R away - little room before TP1"))
    if R > 3 * ctx["atr"]:
        s.warnings.append(f"Wide stop ({R / ctx['atr']:.1f} ATR) - reduce position size")
    if s.entry_type.startswith("limit"):
        s.warnings.append("Limit order: the trade only exists if price retests the entry")
    if ctx["bars"] < 200:
        s.warnings.append(f"Only {ctx['bars']} candles loaded - EMA200 and levels are less reliable")

    s.score = int(max(0, min(100, BASE[s.strategy] + sum(p for p, _ in s.reasons))))
