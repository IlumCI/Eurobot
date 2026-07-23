#!/usr/bin/env python3
"""Valtgeist WATCHLIST — the degen-facing product: a live, self-pruning list of tradeable pools.

Every few hours it posts a fresh, ranked list of pools that are ACTIVE but currently CALM —
real liquidity, not cascading, flow not toxic. Between refreshes it watches each listed pool
live; the instant one turns dangerous (sell-cascade building, toxic dump, or the pod halts)
it posts a CUT and drops it. If the whole list empties before the timer, it refreshes early.

This IS the conviction filter: we only list pools we've vetted as calm, and we only ever
speak up when a vetted pool FLIPS. No firehose — every message is a state change that matters.
It sells information, never an outcome: a watchlist entry means "active and not currently
dumping", not a buy call — hence the disclaimer on every list.

Config (env):
  WATCHLIST_REFRESH_H   hours between full refreshes (default 4)
  WL_MAX                max pools on the list (default 10)
  WL_MIN_LIQ            min pool liquidity USD to be listed (default 50000)
  WL_CALM_N             max Hawkes ratio to be listed as tradeable (default 0.40)
  WL_CALM_VPIN          max VPIN to be listed (default 0.65)
  WL_DANGER_N           Hawkes ratio that gets a listed pool CUT (default 0.70)
  WL_DANGER_VPIN        VPIN (sell-sided) that gets a listed pool CUT (default 0.85)
  WL_MIN_REBUILD_S      floor between (re)builds so an empty list can't spam (default 300)

update() is PURE — it mutates the list and returns the messages to post; the caller does the
network send off the event loop (same split as alerts.scan/send).
"""
import os

from alerts import AlertBook


def _money(v):
    v = float(v or 0)
    if v >= 1e9:
        return f"${v / 1e9:.1f}B"
    if v >= 1e6:
        return f"${v / 1e6:.1f}M"
    if v >= 1e3:
        return f"${v / 1e3:.0f}K"
    return f"${v:.0f}"


def _age(h):
    h = float(h or 0)
    if h >= 24 * 30:
        return f"{h / 24 / 30:.0f}mo"
    if h >= 24:
        return f"{h / 24:.0f}d"
    if h >= 1:
        return f"{h:.0f}h"
    return f"{int(h * 60)}m"


def _base(symbol):
    return (symbol or "?").split("-")[0]


class Watchlist:
    def __init__(self, notifier=None, refresh_h=None, wl_max=None, min_liq=None, calm_n=None,
                 calm_vpin=None, danger_n=None, danger_vpin=None, min_rebuild_s=None):
        self.notifier = notifier or AlertBook()
        self.refresh_s = float(
            refresh_h if refresh_h is not None else os.environ.get("WATCHLIST_REFRESH_H", "4")) * 3600.0
        self.wl_max = int(wl_max if wl_max is not None else os.environ.get("WL_MAX", "10"))
        self.min_liq = float(min_liq if min_liq is not None else os.environ.get("WL_MIN_LIQ", "50000"))
        self.calm_n = float(calm_n if calm_n is not None else os.environ.get("WL_CALM_N", "0.40"))
        self.calm_vpin = float(calm_vpin if calm_vpin is not None else os.environ.get("WL_CALM_VPIN", "0.65"))
        self.danger_n = float(danger_n if danger_n is not None else os.environ.get("WL_DANGER_N", "0.70"))
        self.danger_vpin = float(
            danger_vpin if danger_vpin is not None else os.environ.get("WL_DANGER_VPIN", "0.85"))
        self.min_rebuild_s = float(
            min_rebuild_s if min_rebuild_s is not None else os.environ.get("WL_MIN_REBUILD_S", "300"))
        self.order = []      # symbols, in rank order
        self.listed = {}     # symbol -> entry dict
        self.last_build = None
        self._said_empty = False

    # ------------------------------------------------------------------ classification
    def _liq(self, pod):
        return float((getattr(pod, "metrics", {}) or {}).get("liq") or 0.0)

    def _tradeable(self, pod):
        """Active, liquid, and currently calm — safe to put in front of people as 'tradeable now'."""
        st = getattr(pod, "state", None) or {}
        if st.get("runtime_state") in ("VETOED", "ASHES", "PERCHED"):
            return False
        if (st.get("hawkes_n") or 0.0) >= self.calm_n:
            return False
        vp = st.get("vpin")
        if vp is not None and vp >= self.calm_vpin:
            return False
        if not st.get("price"):
            return False
        return self._liq(pod) >= self.min_liq

    def _danger(self, pod):
        """Why a listed pool must be CUT — returns a degen-readable reason, or None if still fine."""
        st = getattr(pod, "state", None) or {}
        rs = st.get("runtime_state")
        if rs == "VETOED":
            return "venue rug-flagged it"
        if rs == "ASHES":
            return "blew through the safety floor"
        if rs == "PERCHED":
            return "sell-cascade — the bot already bailed"
        n = st.get("hawkes_n") or 0.0
        if n >= self.danger_n:
            return f"sell-cascade building (n={n:.2f}) — dumping"
        vp, imb = st.get("vpin"), st.get("imbalance") or 0.0
        if vp is not None and vp >= self.danger_vpin and imb <= -0.2:
            return f"toxic flow (vpin {vp:.2f}, {imb:+.0%} sell) — one-sided dump"
        return None

    def _entry(self, pod):
        m = getattr(pod, "metrics", {}) or {}
        st = getattr(pod, "state", None) or {}
        return {
            "symbol": pod.symbol,
            "liq": self._liq(pod),
            "vol_h1": float(m.get("vol_h1") or 0.0),
            "turnover": float(m.get("turnover") or 0.0),
            "age_h": float(m.get("age_h") or 0.0),
            "src": st.get("price_src", "jup"),
            "n": st.get("hawkes_n") or 0.0,
        }

    def _score(self, e):
        # degens want ACTIVE: fee turnover weighted by recent move. calm is already guaranteed.
        return e["turnover"] * max(e["vol_h1"], 1.0)

    # ------------------------------------------------------------------ loop-facing (pure)
    def update(self, pods, t):
        """Prune danger, rebuild when due, return the messages to post (no network here)."""
        msgs = []
        by_sym = {p.symbol: p for p in pods}
        # 1) prune: listed pools that turned dangerous, or rotated out of the fleet entirely
        for sym in list(self.order):
            pod = by_sym.get(sym)
            if pod is None:
                self._drop(sym)  # rotated out — drop quietly, no CUT
                continue
            reason = self._danger(pod)
            if reason:
                msgs.append(self._cut_text(sym, reason))
                self._drop(sym)
        # 2) (re)build when: first run, refresh timer elapsed, or the list emptied — floored so an
        #    all-dumping market can't make us spam an empty-list rebuild every tick.
        due = (self.last_build is None or (t - self.last_build) >= self.refresh_s or not self.order)
        can = self.last_build is None or (t - self.last_build) >= self.min_rebuild_s
        if due and can:
            entries = sorted((self._entry(p) for p in pods if self._tradeable(p)),
                             key=self._score, reverse=True)[:self.wl_max]
            self.last_build = t
            if entries:
                self.order = [e["symbol"] for e in entries]
                self.listed = {e["symbol"]: e for e in entries}
                self._said_empty = False
                msgs.append(self._list_text(entries))
            elif not self._said_empty:
                self._said_empty = True
                msgs.append(self._empty_text())
        return msgs

    def _drop(self, sym):
        self.order = [s for s in self.order if s != sym]
        self.listed.pop(sym, None)

    # ------------------------------------------------------------------ degen copy
    def _list_text(self, entries):
        lines = []
        for e in entries:
            heat = "🟢" if e["n"] < 0.2 else "🟡"
            live = "⚡" if e["src"] == "ws" else "·"
            lines.append(
                f"{heat} ${_base(e['symbol'])}  ·  {_money(e['liq'])} liq  ·  "
                f"🔥{e['vol_h1']:.0f}%/h  ·  {e['turnover']:.1f}x/day  ·  {_age(e['age_h'])}  {live}")
        n = len(entries)
        return (f"🎯 TRADEABLE NOW · {n} pool{'s' if n != 1 else ''}\n"
                f"active, liquid & not dumping. we watch these live and call it the second one turns 👇\n\n"
                + "\n".join(lines)
                + "\n\n🟢 calm · 🟡 warming · ⚡ live sub-second feed"
                + "\n⚠️ not advice — any of these can rug. we post when they flip. dyor.")

    def _cut_text(self, sym, reason):
        return (f"☠️ CUT ${_base(sym)} — {reason}.\n"
                f"if you're LPing this, you're the exit liquidity. off the list.")

    def _empty_text(self):
        return ("🌑 nothing clean right now — every active pool is dumping or too thin.\n"
                "hunting for a fresh list…")


if __name__ == "__main__":
    # self-test: build a list -> steady (silent) -> CUT on flip -> empty triggers refresh. No network.
    class _P:
        def __init__(self, symbol, metrics, state):
            self.symbol, self.metrics, self.state = symbol, metrics, state

    def pod(sym, liq=300000, vol=10, turn=4, age=500, n=0.0, vpin=0.3, imb=0.0, rs="FLYING", src="ws"):
        return _P(sym, {"liq": liq, "vol_h1": vol, "turnover": turn, "age_h": age},
                  {"runtime_state": rs, "hawkes_n": n, "vpin": vpin, "imbalance": imb,
                   "price": 1.0, "price_src": src})

    wl = Watchlist(notifier=AlertBook(token="", chat_id=""),
                   refresh_h=4, min_liq=50000, min_rebuild_s=0)

    pods = [pod("NORMIE-SOL", turn=5, vol=12), pod("KET-SOL", turn=8, vol=6), pod("BONK-SOL", turn=3, vol=20)]
    m1 = wl.update(pods, t=0)
    assert len(m1) == 1 and "TRADEABLE NOW · 3 pools" in m1[0], m1
    assert set(wl.order) == {"NORMIE-SOL", "KET-SOL", "BONK-SOL"}, wl.order

    # steady state: nothing changed, list non-empty, timer not due -> silence (the point of the filter)
    assert wl.update(pods, t=10) == []

    # KET flips toxic -> exactly one CUT, dropped from the list, others untouched
    pods[1] = pod("KET-SOL", n=0.9, vpin=0.9, imb=-0.6)
    m3 = wl.update(pods, t=20)
    assert len(m3) == 1 and m3[0].startswith("☠️ CUT $KET"), m3
    assert "KET-SOL" not in wl.order and len(wl.order) == 2

    # the rest go dangerous -> both CUT, list empties -> rebuild finds nothing -> one "empty" note
    pods[0] = pod("NORMIE-SOL", rs="PERCHED", n=0.9)
    pods[2] = pod("BONK-SOL", n=0.85)
    m4 = wl.update(pods, t=30)
    assert sum(x.startswith("☠️ CUT") for x in m4) == 2 and any("nothing clean" in x for x in m4), m4
    assert wl.order == []

    # market calms with a fresh name -> empty list rebuilds and posts again
    m5 = wl.update([pod("WIF-SOL", turn=6, vol=15)], t=40)
    assert len(m5) == 1 and "TRADEABLE NOW · 1 pool" in m5[0], m5

    print(wl._list_text([wl._entry(pod("NORMIE-SOL", liq=320000, vol=12, turn=4.4, age=510)),
                         wl._entry(pod("KET-SOL", liq=115000, vol=39, turn=22.6, age=155, n=0.3, src="jup"))]))
    print()
    print(wl._cut_text("旺旺-SOL", "sell-cascade building (n=0.81) — dumping"))
    print("\nwatchlist self-test OK")
