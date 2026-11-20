"""Terminal report, JSON export and an interactive HTML chart."""
import json

from .setups import fmt

W = 92
USE_COLOR = True


def c(txt, code):
    return f"\033[{code}m{txt}\033[0m" if USE_COLOR else str(txt)


def bar(score, width=20):
    n = round(score / 100 * width)
    return "█" * n + "░" * (width - n)


def market_overview(ctx) -> str:
    i, st, px, a = ctx["ind"], ctx["structure"], ctx["px"], ctx["atr"]
    out = ["", c("═" * W, "1"),
           c(f" {ctx['symbol']}  ·  {ctx['interval']}  ·  last candle {ctx['time']:%Y-%m-%d %H:%M} UTC  ·  {ctx['bars']} bars", "1"),
           c("═" * W, "1"),
           f" Price      {fmt(px)}  ({ctx['change_pct']:+.2f}% last bar)    ATR(14) {fmt(a)} ({100 * a / px:.2f}%)",
           f" Trend      {c(ctx['trend_label'], '1')}"]
    seq = st["sequence"] or "not enough swings"
    out.append(f" Structure  {st['trend']}  ·  recent swings: {seq}")
    if ctx["bos"]:
        b = ctx["bos"]
        out.append(f"            {b['type']} {b['direction']} through {fmt(b['level'])} ({b['bars_ago']} bars ago)")
    ema_pos = lambda n: ("above" if px > i[n] else "below")
    out += ["", c(" Indicators", "1"),
            f"   EMA20 {fmt(i['ema20'])}  EMA50 {fmt(i['ema50'])}  EMA200 {fmt(i['ema200'])}  → price is "
            f"{ema_pos('ema20')}/{ema_pos('ema50')}/{ema_pos('ema200')} them",
            f"   RSI(14) {i['rsi']:.1f} {rsi_word(i['rsi'])}   Stoch %K {i['stoch_k']:.0f}   "
            f"ADX {i['adx']:.1f} (+DI {i['plus_di']:.0f} / -DI {i['minus_di']:.0f})",
            f"   MACD {i['macd']:+.4g} vs signal {i['macd_signal']:+.4g}  (hist {i['macd_hist']:+.4g}, "
            f"{'rising' if i['macd_hist'] > ctx['macd_hist_prev'] else 'falling'})",
            f"   Bollinger {fmt(i['bb_low'])} – {fmt(i['bb_up'])}  ·  %B {bb_pct(px, i):.0f}%"
            + (f"   ·  Volume {ctx['vol_ratio']:.1f}x avg" if ctx["vol_ratio"] is not None else "")]
    if ctx["divergence"]:
        out.append(f"   {ctx['divergence'].capitalize()} RSI divergence on the latest swings")
    if ctx["candle_txt"]:
        out.append(f"   Last candle: {ctx['candle_txt']}")
    out += ["", c(" Key levels (strongest swing clusters)", "1")]
    res = [L for L in ctx["levels"] if L.price > px][:4]
    sup = [L for L in reversed(ctx["levels"]) if L.price <= px][:4]
    if not res:
        out.append("   (no resistance overhead - price at/near its highs)")
    for L in reversed(res):
        out.append(f"   {c('R', '31')}  {fmt(L.price):>14}  {L.role:<19} touches {L.touches}  "
                   f"{100 * (L.price / px - 1):+6.2f}%  strength {L.strength}")
    out.append(f"   {'─' * 4} {fmt(px):>12}  ← price")
    for L in sup:
        out.append(f"   {c('S', '32')}  {fmt(L.price):>14}  {L.role:<19} touches {L.touches}  "
                   f"{100 * (L.price / px - 1):+6.2f}%  strength {L.strength}")
    return "\n".join(out)


def rsi_word(r):
    return "(overbought)" if r > 70 else "(oversold)" if r < 30 else "(bullish zone)" if r > 55 else \
        "(bearish zone)" if r < 45 else "(neutral)"


def bb_pct(px, i):
    w = i["bb_up"] - i["bb_low"]
    return 100 * (px - i["bb_low"]) / w if w else 50


def setup_block(s, rank, account=None, risk_pct=None) -> str:
    col = "32" if s.side == "BUY" else "31"
    out = ["", c(f" #{rank}  {s.side} {s.symbol} - {s.strategy}", "1;" + col)
           + f"   score {s.score}/100 {bar(s.score)}  grade {c(s.grade, '1')} · {s.verdict}",
           f"     Trigger : {s.trigger}",
           f"     Entry   : {c(fmt(s.entry), '1')}  ({s.entry_type})",
           f"     Stop    : {c(fmt(s.stop), '1')}  risk {fmt(s.risk)} ({s.risk_pct:.2f}%)  ← {s.stop_basis}"]
    for k, t in enumerate(s.targets, 1):
        pct = 100 * abs(t.price - s.entry) / s.entry
        out.append(f"     TP{k}     : {c(fmt(t.price), '1')}  R:R 1:{t.rr:.2f}  (+{pct:.2f}%)  ← {t.basis}")
    if account and risk_pct:
        units = account * risk_pct / 100 / s.risk
        out.append(f"     Size    : {units:,.4g} units ≈ {fmt(units * s.entry)} notional to risk "
                   f"{risk_pct}% ({fmt(account * risk_pct / 100)}) of {fmt(account)}")
    pos, neg = s.summary()
    out.append(c("     Why this trade:", "1"))
    out += [f"       + {t}" for t in pos] or ["       (no strong confluence)"]
    if neg:
        out.append(c("     Risks / against:", "1"))
        out += [f"       - {t}" for t in neg]
    out.append(f"     Invalidation: {s.invalidation}")
    return "\n".join(out)


def ranking_table(setups) -> str:
    hdr = f" {'#':>2}  {'Symbol':<12}{'Side':<5}{'Strategy':<16}{'Score':>6} {'Gr':>3} {'Entry':>13} {'Stop':>13} {'TP1 R':>6} {'TP2 R':>6} {'TP3 R':>6}"
    out = ["", c("═" * W, "1"), c(" RANKED OPPORTUNITIES (by technical strength)", "1"), c("═" * W, "1"), hdr, " " + "─" * (W - 2)]
    for k, s in enumerate(setups, 1):
        out.append(f" {k:>2}  {s.symbol[:11]:<12}{c(f'{s.side:<5}', '32' if s.side == 'BUY' else '31')}"
                   f"{s.strategy:<16}{s.score:>6} {s.grade:>3} {fmt(s.entry):>13} {fmt(s.stop):>13} "
                   + " ".join(f"{t.rr:>6.2f}" for t in s.targets))
    return "\n".join(out)


def to_dict(ctx) -> dict:
    return {
        "symbol": ctx["symbol"], "interval": ctx["interval"], "time": ctx["time"].isoformat(), "price": ctx["px"],
        "trend": ctx["trend_label"], "structure": ctx["structure"]["trend"], "swing_sequence": ctx["structure"]["sequence"],
        "break_of_structure": ctx["bos"], "divergence": ctx["divergence"], "candle": ctx["candle_txt"],
        "indicators": {k: round(v, 6) for k, v in ctx["ind"].items()},
        "levels": [{"price": L.price, "role": L.role, "touches": L.touches, "strength": L.strength} for L in ctx["levels"]],
        "setups": [{
            "side": s.side, "strategy": s.strategy, "score": s.score, "grade": s.grade, "verdict": s.verdict,
            "trigger": s.trigger, "entry": s.entry, "entry_type": s.entry_type, "stop": s.stop, "stop_basis": s.stop_basis,
            "risk_pct": round(s.risk_pct, 3),
            "targets": [{"price": t.price, "rr": t.rr, "basis": t.basis} for t in s.targets],
            "reasons": [{"points": p, "text": t} for p, t in s.reasons], "warnings": s.warnings,
            "invalidation": s.invalidation} for s in ctx["setups"]],
    }


def write_json(results, path):
    with open(path, "w") as f:
        json.dump([to_dict(r) for r in results], f, indent=2, default=str)


def write_chart(ctx, path, bars=200):
    """Interactive candlestick chart (Plotly) with EMAs, S/R levels and the top setup's entry/SL/TPs."""
    df = ctx["df"].iloc[-bars:]
    x = [t.isoformat() for t in df.index]
    traces = [{"type": "candlestick", "x": x, "open": df["open"].tolist(), "high": df["high"].tolist(),
               "low": df["low"].tolist(), "close": df["close"].tolist(), "name": ctx["symbol"],
               "increasing": {"line": {"color": "#26a69a"}}, "decreasing": {"line": {"color": "#ef5350"}}}]
    for n, col in (("ema20", "#f6c344"), ("ema50", "#4f8ff7"), ("ema200", "#b06ef3")):
        traces.append({"type": "scatter", "mode": "lines", "x": x, "y": df[n].round(8).tolist(), "name": n.upper(),
                       "line": {"width": 1.4, "color": col}})
    shapes, notes = [], []

    def hline(y, color, dash, label, width=1):
        shapes.append({"type": "line", "xref": "paper", "x0": 0, "x1": 1, "y0": y, "y1": y,
                       "line": {"color": color, "width": width, "dash": dash}})
        notes.append({"xref": "paper", "x": 1, "y": y, "xanchor": "left", "text": label, "showarrow": False,
                      "font": {"size": 10, "color": color}})

    lo, hi = df["low"].min(), df["high"].max()
    for L in ctx["levels"]:
        if lo * 0.97 <= L.price <= hi * 1.03:
            hline(L.price, "#8a8f98", "dot", f"{L.role} ×{L.touches}")
    if ctx["setups"]:
        s = ctx["setups"][0]
        hline(s.entry, "#4f8ff7", "solid", f"{s.side} entry {fmt(s.entry)}", 2)
        hline(s.stop, "#ef5350", "dash", f"SL {fmt(s.stop)}", 2)
        for k, t in enumerate(s.targets, 1):
            hline(t.price, "#26a69a", "dash", f"TP{k} {fmt(t.price)} ({t.rr}R)", 1.5)
        title = f"{ctx['symbol']} {ctx['interval']} - top setup: {s.side} {s.strategy} (score {s.score})"
    else:
        title = f"{ctx['symbol']} {ctx['interval']} - no qualifying setup"
    grid = {"gridcolor": "#2a2e39", "zerolinecolor": "#2a2e39"}
    layout = {"title": {"text": title}, "paper_bgcolor": "#131722", "plot_bgcolor": "#131722",
              "font": {"color": "#d1d4dc"}, "height": 720, "margin": {"r": 170},
              "xaxis": {"rangeslider": {"visible": False}, **grid}, "yaxis": grid, "shapes": shapes, "annotations": notes,
              "legend": {"orientation": "h", "y": -0.08}}
    html = f"""<!doctype html><html><head><meta charset="utf-8"><title>{ctx['symbol']} chart</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<script src="https://cdn.jsdelivr.net/npm/plotly.js-dist-min@2.35.2/plotly.min.js"></script>
<style>body{{margin:0;background:#131722;color:#ddd;font-family:system-ui}}</style></head>
<body><div id="c"></div><script>
Plotly.newPlot("c", {json.dumps(traces)}, {json.dumps(layout)}, {{responsive: true}});
</script></body></html>"""
    with open(path, "w") as f:
        f.write(html)
