#!/usr/bin/env python3
"""Candlestick PNG renderer for the watchlist — GeckoTerminal OHLCV -> Pillow image.

Deliberately NOT a headless-browser screenshot: on a 1-vCPU box that would be slow and heavy.
Instead we pull the raw OHLCV candles from GeckoTerminal (same API the fleet already uses) and
draw the candles ourselves with Pillow (~150-250ms, CPU-cheap). The caller runs this off the
event loop and falls back to a text-only post if anything here returns None.

chart_png(addr, sym, kind) is the entry point:
  kind="tradeable" -> 15m candles, ~12h context, orange accent (a pool we're listing)
  kind="dying"     -> 5m candles,  ~5h zoom,     red accent    (a pool we're cutting)
"""
import io
import json
import urllib.request

GT = "https://api.geckoterminal.com/api/v2/networks/solana"


def _fmt_px(p):
    try:
        p = float(p)
    except (TypeError, ValueError):
        return "?"
    if p <= 0:
        return "?"
    if p >= 1:
        return f"{p:,.4f}"
    import math
    digits = min(12, max(4, 2 - int(math.floor(math.log10(p)))))
    return f"{p:.{digits}f}"


def fetch_ohlcv(addr, timeframe="minute", aggregate=15, limit=48):
    """-> list of (o,h,l,c) oldest->newest, or None. GeckoTerminal returns newest-first.

    timeframe is one of GeckoTerminal's day|hour|minute; aggregate is valid only within it
    (minute: 1/5/15, hour: 1/4/12) — so hourly candles need timeframe=hour, NOT minute/60.
    """
    url = f"{GT}/pools/{addr}/ohlcv/{timeframe}?aggregate={aggregate}&limit={limit}"
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "valtgeist"})
        with urllib.request.urlopen(req, timeout=12) as r:
            d = json.load(r)
        lst = d["data"]["attributes"]["ohlcv_list"]
        rows = [(float(o), float(h), float(low), float(c)) for _ts, o, h, low, c, _v in lst]
        rows.reverse()
        return [r for r in rows if r[1] >= r[2] and r[1] > 0]  # drop malformed candles
    except Exception:
        return None


def _render(rows, title, accent):
    from PIL import Image, ImageDraw, ImageFont

    W, H, mr, mt, mb, ml = 900, 460, 104, 46, 26, 14
    bg, grid, up, down, mut = (18, 19, 16), (38, 40, 36), (38, 162, 105), (224, 27, 36), (120, 122, 116)
    img = Image.new("RGB", (W, H), bg)
    dr = ImageDraw.Draw(img)

    def font(sz):
        try:
            return ImageFont.load_default(sz)
        except TypeError:
            return ImageFont.load_default()

    hi = max(r[1] for r in rows)
    lo = min(r[2] for r in rows)
    if hi <= lo:
        hi = lo * 1.001 + 1e-12
    pad = (hi - lo) * 0.08
    hi += pad
    lo -= pad
    x0, x1, y0, y1 = ml, W - mr, mt, H - mb

    def X(i):
        return x0 + (x1 - x0) * (i + 0.5) / len(rows)

    def Y(p):
        return y1 - (y1 - y0) * (p - lo) / (hi - lo)

    for k in range(5):                       # price gridlines + right-edge labels
        p = lo + (hi - lo) * k / 4
        y = Y(p)
        dr.line([(x0, y), (x1, y)], fill=grid)
        dr.text((x1 + 6, y - 7), _fmt_px(p), font=font(13), fill=mut)

    cw = max(2.0, (x1 - x0) / len(rows) * 0.62)
    for i, (o, h, low, c) in enumerate(rows):
        col = up if c >= o else down
        cx = X(i)
        dr.line([(cx, Y(h)), (cx, Y(low))], fill=col)      # wick
        top, bot = sorted((Y(o), Y(c)))
        if bot - top < 1:
            bot = top + 1
        dr.rectangle([cx - cw / 2, top, cx + cw / 2, bot], fill=col)  # body

    last, first = rows[-1][3], rows[0][3]
    chg = (last / first - 1) * 100 if first else 0.0
    yl = Y(last)
    dr.line([(x0, yl), (x1, yl)], fill=accent)             # current-price line
    dr.text((ml, 12), title, font=font(18), fill=accent)
    lbl = f"{_fmt_px(last)}   {chg:+.1f}%"
    tw = dr.textlength(lbl, font=font(15))
    dr.text((x1 - tw, 15), lbl, font=font(15), fill=(up if chg >= 0 else down))

    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def chart_png(addr, sym, kind="tradeable"):
    """Fetch + render. Returns PNG bytes, or None on any failure (caller falls back to text)."""
    if not addr:
        return None
    if kind == "dying":
        rows, tf, accent = fetch_ohlcv(addr, "minute", 5, 60), "5m", (224, 27, 36)
    elif kind == "stable":
        rows, tf, accent = fetch_ohlcv(addr, "hour", 1, 48), "1h", (38, 162, 105)
    else:
        rows, tf, accent = fetch_ohlcv(addr, "minute", 15, 48), "15m", (255, 90, 31)
    if not rows or len(rows) < 4:
        return None
    base = (sym or "?").split("-")[0]
    try:
        return _render(rows, f"{base}   ·   {tf}   ·   valtgeist", accent)
    except Exception:
        return None


if __name__ == "__main__":
    import sys
    a = sys.argv[1] if len(sys.argv) > 1 else "8N544CG9j44dkzu4CjSWHxpwekxHQPTR4R17Kw9y5FBk"
    s = sys.argv[2] if len(sys.argv) > 2 else "NORMIE-SOL"
    for kind in ("tradeable", "dying"):
        png = chart_png(a, s, kind)
        if png:
            fn = f"/tmp/chart_{kind}.png"
            with open(fn, "wb") as f:
                f.write(png)
            print(f"{kind}: wrote {len(png)} bytes -> {fn}")
        else:
            print(f"{kind}: no chart (fetch/render failed)")
