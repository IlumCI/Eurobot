#!/usr/bin/env python3
"""TOKEN OF THE HOUR — the calm counterpoint to the watchlist.

The watchlist hunts ACTIVE pools; this does the exact opposite. About once an hour it scans the
same candidate universe and surfaces ONE pool that's slow but solid — deep liquidity, low
volatility, gently positive on the day. It's a teaching post ("this is what stable looks like")
to sit next to the CUT/dump charts, NOT a buy call: everything is described factually (liq, 24h,
volatility, age) and it carries the same not-advice disclaimer. Selected for low volatility, not
predicted to rise.

Config (env):
  STABLE_EVERY_H     hours between posts (default 1)
  STABLE_MIN_LIQ     min liquidity USD (default 150000 — stable == deep, exitable)
  STABLE_MAX_VOL1    max 1h move % to count as calm (default 6)
  STABLE_MAX_VOL6    max 6h move % (default 18)
  STABLE_MIN_AGE_H   min pool age hours (default 168 = 7d — seasoned, not a fresh launch)
  STABLE_MAX_CH24    max 24h change % (default 60 — above this it's a pump, not 'stable')
"""
import math
import os
import time


class StablePick:
    def __init__(self, notifier=None, every_h=None):
        from alerts import AlertBook

        def _f(v, key, dflt):
            return float(v if v is not None else os.environ.get(key, dflt))

        self.notifier = notifier or AlertBook()
        self.every_s = _f(every_h, "STABLE_EVERY_H", "1") * 3600.0
        self.min_liq = _f(None, "STABLE_MIN_LIQ", "150000")
        self.max_vol1 = _f(None, "STABLE_MAX_VOL1", "6")
        self.max_vol6 = _f(None, "STABLE_MAX_VOL6", "18")
        self.min_age_h = _f(None, "STABLE_MIN_AGE_H", "168")
        self.max_ch24 = _f(None, "STABLE_MAX_CH24", "20")   # >20%/day isn't "stable", it's a move
        self.last = None        # last time the hourly slot was claimed (drives the cadence)
        self.recent = []        # last few featured symbols, to avoid repeating the same token

    def due(self, t):
        return self.last is None or (t - self.last) >= self.every_s

    def _candidates(self):
        # reuse the fleet's GeckoTerminal fetch + metric parse (fleet is already imported by then)
        from fleet import _gt_pools, _metrics
        now = time.time()
        out = []
        for p in _gt_pools():
            try:
                out.append(_metrics(p, now))
            except Exception:
                pass
        return out

    def _stable(self, m):
        return bool(
            m.get("addr")
            and m["liq"] >= self.min_liq
            and m["vol_h1"] <= self.max_vol1
            and m["vol_h6"] <= self.max_vol6
            and m["age_h"] >= self.min_age_h
            and 0.0 <= m["ch_h24"] <= self.max_ch24   # rising/flat, not dumping, not a pump
        )

    def _score(self, m):
        # reward gentle uptrend + deep liquidity + age; penalise volatility. calm is the point.
        rising = min(m["ch_h24"], 40.0)
        return (rising - 2.0 * m["vol_h1"] - m["vol_h6"]
                + 5.0 * math.log10(max(m["liq"], 1.0)) + math.log10(max(m["age_h"], 1.0)))

    def pick(self, t, exclude=()):
        cands = [m for m in self._candidates() if self._stable(m)]
        fresh = [m for m in cands if m["symbol"] not in exclude and m["symbol"] not in self.recent]
        pool = fresh or [m for m in cands if m["symbol"] not in exclude]  # allow a repeat if nothing new
        if not pool:
            return None
        best = max(pool, key=self._score)
        self.recent = (self.recent + [best["symbol"]])[-6:]
        return best

    def build(self, t, exclude=()):
        """Scan (network) and return a post-item, or None. The caller gates on due() and claims the
        hourly slot BEFORE calling this, then runs it in the background so the main loop never blocks
        on the GeckoTerminal fetch. Genuinely-calm tokens are rare, so most scans return None."""
        m = self.pick(t, exclude)
        if not m:
            return None
        return {"text": self._text(m), "sym": m["symbol"], "addr": m["addr"], "chart": "stable"}

    def _text(self, m):
        from alerts import _fmt_price
        from watchlist import _age, _base, _money
        ch = m["ch_h24"]
        trend = "quietly up" if ch >= 2 else "holding steady"
        return (
            "🟩 TOKEN OF THE HOUR — what \"stable\" looks like\n"
            "everything else is dumping. this one's just… steady. 👇\n\n"
            f"${_base(m['symbol'])}\n"
            f"• mcap:      {_money(m['mcap'])}\n"
            f"• price:     {_fmt_price(m['price_usd'])}\n"
            f"• liquidity: {_money(m['liq'])}\n"
            f"• 24h:       {ch:+.0f}%  ({trend})\n"
            f"• 1h move:   {m['vol_h1']:.0f}%  (calm)\n"
            f"• age:       {_age(m['age_h'])}\n"
            f"• CA:        {m.get('base_mint')}\n\n"
            "not on our tradeable list — too slow for that. it's here to show what healthy looks "
            "like next to the rugs we flag.\n"
            "⚠️ not advice — even calm tokens can turn. dyor."
        )


if __name__ == "__main__":
    # self-test: classification + scoring + copy, no network.
    sp = StablePick(notifier=None, every_h=1)
    sp.notifier = type("N", (), {"live": False, "post": lambda *a, **k: False})()

    def m(sym, liq, v1, v6, age, ch24, ca="So1111", mcap=5e6, px=1.23):
        return {"addr": "pool_" + sym, "symbol": sym, "liq": liq, "mcap": mcap, "price_usd": px,
                "vol_h1": v1, "vol_h6": v6, "age_h": age, "ch_h24": ch24, "base_mint": ca}

    good = m("STEADY-SOL", 800000, 2, 5, 900, 6)
    assert sp._stable(good)
    assert not sp._stable(m("PUMP-SOL", 800000, 2, 5, 900, 120))    # +120% = pump, excluded
    assert not sp._stable(m("DUMP-SOL", 800000, 2, 5, 900, -20))    # dumping, excluded
    assert not sp._stable(m("CHOP-SOL", 800000, 30, 40, 900, 6))    # too volatile
    assert not sp._stable(m("FRESH-SOL", 800000, 2, 5, 3, 6))       # too new
    assert not sp._stable(m("THIN-SOL", 5000, 2, 5, 900, 6))        # too thin
    # of two stable ones, deeper liquidity + gentler uptrend scores higher
    assert sp._score(m("A", 900000, 1, 3, 900, 8)) > sp._score(m("B", 200000, 5, 12, 300, 3))
    print(sp._text(good))
    print("\nstable_pick self-test OK")
