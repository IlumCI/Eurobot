#!/usr/bin/env python3
"""Valtgeist WATCHLIST — the degen-facing product: a live, self-pruning list of tradeable pools.

Every few hours it posts a fresh, ranked list of pools that are ACTIVE but genuinely CALM —
real liquidity, quoting (warmed up), not cascading, flow not toxic. Between refreshes it watches
each listed pool live and CUTs it the moment it actually turns: a real price dump, the pod
halting, or SUSTAINED heavy sell pressure. If the whole list empties before the timer, it
refreshes early.

Calibrated for pump.fun reality: on that market almost everything is momentarily "cascading",
so a naive cascade-cut nukes the whole list at once. Guards against that:
  - only FLYING pods are listed  -> "calm" means MEASURED calm, not a cold pod with no data yet
  - a grace window after listing  -> a fresh pick can't be cut by a one-tick flicker
  - danger must PERSIST N cycles   -> single spikes are ignored
  - a real price drawdown cuts hard -> the honest "it's dying" signal, not the noisy cascade score

This IS the conviction filter: only vetted-calm pools are listed, and we only ever speak when a
vetted pool genuinely flips. It sells information, never an outcome — a listing means "active and
not currently dumping", not a buy call. Hence the disclaimer on every list.

Config (env):
  WATCHLIST_REFRESH_H   hours between full refreshes (default 4)
  WL_MAX                max pools on the list (default 10)
  WL_MIN_LIQ            min pool liquidity USD to be listed (default 50000)
  WL_CALM_N             max Hawkes ratio to be listed as tradeable (default 0.40)
  WL_CALM_VPIN          max VPIN to be listed (default 0.65)
  WL_DANGER_N           Hawkes ratio (sustained) that CUTs a listed pool (default 0.85)
  WL_DANGER_VPIN        VPIN (sustained, sell-sided) that CUTs a listed pool (default 0.90)
  WL_DROP_PCT           price drop from listing price that CUTs immediately (default 0.12)
  WL_TRAIL_PCT          drop from the pool's post-listing high that CUTs (default 0.15)
  WL_GRACE_S            seconds after listing before a soft (micro) signal may cut (default 90)
  WL_DANGER_CYCLES      consecutive cycles a soft signal must hold before a cut (default 3)
  WL_MIN_REBUILD_S      floor between rebuilds so an all-dumping market can't spam (default 180)

update() is PURE — it mutates the list and returns the messages to post; the caller sends off
the event loop (same split as alerts.scan/send).
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
                 calm_vpin=None, danger_n=None, danger_vpin=None, drop_pct=None, trail_pct=None,
                 grace_s=None, danger_cycles=None, min_rebuild_s=None):
        self.notifier = notifier or AlertBook()

        def _f(v, key, dflt):
            return float(v if v is not None else os.environ.get(key, dflt))

        self.refresh_s = _f(refresh_h, "WATCHLIST_REFRESH_H", "4") * 3600.0
        self.wl_max = int(wl_max if wl_max is not None else os.environ.get("WL_MAX", "10"))
        self.min_liq = _f(min_liq, "WL_MIN_LIQ", "50000")
        self.calm_n = _f(calm_n, "WL_CALM_N", "0.40")
        self.calm_vpin = _f(calm_vpin, "WL_CALM_VPIN", "0.65")
        self.danger_n = _f(danger_n, "WL_DANGER_N", "0.85")
        self.danger_vpin = _f(danger_vpin, "WL_DANGER_VPIN", "0.90")
        self.drop_pct = _f(drop_pct, "WL_DROP_PCT", "0.12")
        self.trail_pct = _f(trail_pct, "WL_TRAIL_PCT", "0.15")
        self.grace_s = _f(grace_s, "WL_GRACE_S", "90")
        self.danger_cycles = int(danger_cycles if danger_cycles is not None
                                 else os.environ.get("WL_DANGER_CYCLES", "3"))
        self.min_rebuild_s = _f(min_rebuild_s, "WL_MIN_REBUILD_S", "180")
        self.order = []      # symbols, in rank order
        self.listed = {}     # symbol -> entry dict (with added_t / list_price / peak_price / streak)
        self.last_build = None
        self._said_empty = False

    # ------------------------------------------------------------------ classification
    def _liq(self, pod):
        return float((getattr(pod, "metrics", {}) or {}).get("liq") or 0.0)

    def _tradeable(self, pod):
        """Warmed-up, liquid, and genuinely calm — safe to put in front of people as 'tradeable now'.

        Requires FLYING: a HATCHING pod's low cascade score is just missing data, not calm.
        """
        st = getattr(pod, "state", None) or {}
        if st.get("runtime_state") != "FLYING":
            return False
        if (st.get("hawkes_n") or 0.0) >= self.calm_n:
            return False
        vp = st.get("vpin")
        if vp is not None and vp >= self.calm_vpin:
            return False
        if not st.get("price"):
            return False
        return self._liq(pod) >= self.min_liq

    def _classify(self, pod, entry):
        """Why a listed pool must be CUT. Returns (reason, hard): hard=True cuts now, hard=False is
        a soft micro-signal that only cuts after the grace window + sustained for N cycles."""
        st = getattr(pod, "state", None) or {}
        rs = st.get("runtime_state")
        if rs == "VETOED":
            return ("venue rug-flagged it", True)
        if rs == "ASHES":
            return ("blew through the safety floor", True)
        if rs == "PERCHED":
            return ("sell-cascade — the bot already bailed", True)
        price = st.get("price")
        lp, pk = entry.get("list_price"), entry.get("peak_price")
        if price and lp and price <= lp * (1 - self.drop_pct):
            return (f"dumped {price / lp - 1:+.0%} since we listed it", True)
        if price and pk and price <= pk * (1 - self.trail_pct):
            return (f"crashed {price / pk - 1:+.0%} off its high", True)
        n = st.get("hawkes_n") or 0.0
        if n >= self.danger_n:
            return (f"heavy sell pressure building (n={n:.2f})", False)
        vp, imb = st.get("vpin"), st.get("imbalance") or 0.0
        if vp is not None and vp >= self.danger_vpin and imb <= -0.2:
            return (f"toxic sell flow (vpin {vp:.2f})", False)
        return (None, False)

    def _entry(self, pod):
        m = getattr(pod, "metrics", {}) or {}
        st = getattr(pod, "state", None) or {}
        return {
            "symbol": pod.symbol,
            "liq": self._liq(pod),
            "vol_h1": float(m.get("vol_h1") or 0.0),
            "turnover": float(m.get("turnover") or 0.0),
            "age_h": float(m.get("age_h") or 0.0),
            "price": st.get("price"),
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
            e = self.listed[sym]
            price = (getattr(pod, "state", None) or {}).get("price")
            if price:
                e["peak_price"] = max(e.get("peak_price") or price, price)
            reason, hard = self._classify(pod, e)
            if reason and hard:
                msgs.append(self._cut_text(sym, reason))
                self._drop(sym)
            elif reason and (t - e["added_t"]) >= self.grace_s:
                e["streak"] = e.get("streak", 0) + 1
                if e["streak"] >= self.danger_cycles:
                    msgs.append(self._cut_text(sym, reason))
                    self._drop(sym)
            elif not reason:
                e["streak"] = 0
        # 2) (re)build when: first run, refresh timer elapsed, or the list emptied — floored so an
        #    all-dumping market can't spam. Empty results don't set last_build, so startup keeps
        #    retrying (silently) until pods warm up to FLYING, then posts the first real list.
        due = (self.last_build is None or (t - self.last_build) >= self.refresh_s or not self.order)
        can = self.last_build is None or (t - self.last_build) >= self.min_rebuild_s
        if due and can:
            entries = sorted((self._entry(p) for p in pods if self._tradeable(p)),
                             key=self._score, reverse=True)[:self.wl_max]
            if entries:
                for e in entries:
                    e.update(added_t=t, list_price=e["price"], peak_price=e["price"], streak=0)
                self.order = [e["symbol"] for e in entries]
                self.listed = {e["symbol"]: e for e in entries}
                self.last_build = t
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
    # self-test: FLYING-only listing, hard cuts (halt / price dump), soft cut needs persistence.
    class _P:
        def __init__(self, symbol, metrics, state):
            self.symbol, self.metrics, self.state = symbol, metrics, state

    def pod(sym, liq=300000, vol=10, turn=4, age=500, n=0.0, vpin=0.3, imb=0.0,
            rs="FLYING", src="ws", price=1.0):
        return _P(sym, {"liq": liq, "vol_h1": vol, "turnover": turn, "age_h": age},
                  {"runtime_state": rs, "hawkes_n": n, "vpin": vpin, "imbalance": imb,
                   "price": price, "price_src": src})

    wl = Watchlist(notifier=AlertBook(token="", chat_id=""), refresh_h=4, min_liq=50000,
                   min_rebuild_s=0, grace_s=0, danger_cycles=2, drop_pct=0.12, trail_pct=0.15)

    # only FLYING pods get listed — a HATCHING one is excluded (its calm is unmeasured)
    pods = [pod("A-SOL", turn=5, vol=12, price=1.0), pod("B-SOL", turn=8, vol=6, price=2.0),
            pod("C-SOL", turn=3, vol=20, price=0.01), pod("COLD-SOL", rs="HATCHING")]
    m1 = wl.update(pods, t=0)
    assert len(m1) == 1 and "TRADEABLE NOW · 3 pools" in m1[0], m1
    assert set(wl.order) == {"A-SOL", "B-SOL", "C-SOL"}, wl.order

    # steady: nothing changed -> silence
    assert wl.update(pods, t=10) == []

    # hard cut 1: pod halts (PERCHED) -> immediate
    pods[0] = pod("A-SOL", rs="PERCHED")
    ma = wl.update(pods, t=20)
    assert len(ma) == 1 and ma[0].startswith("☠️ CUT $A") and "A-SOL" not in wl.order, ma

    # hard cut 2: real price dump (-25% from listing 2.0 -> 1.5) -> immediate
    pods[1] = pod("B-SOL", price=1.5)
    mb = wl.update(pods, t=30)
    assert any(x.startswith("☠️ CUT $B") and "dumped -25%" in x for x in mb), mb

    # soft signal needs persistence: high n once -> NO cut; twice -> cut
    pods[2] = pod("C-SOL", n=0.9, price=0.01)
    ms1 = wl.update(pods, t=40)
    assert not any("CUT $C" in x for x in ms1), ms1        # streak=1, held
    ms2 = wl.update(pods, t=50)
    assert any("CUT $C" in x for x in ms2), ms2            # streak=2 -> cut

    print(wl._list_text([wl._entry(pod("NORMIE-SOL", liq=320000, vol=12, turn=4.4, age=510)),
                         wl._entry(pod("KET-SOL", liq=115000, vol=39, turn=22.6, age=155, n=0.3, src="jup"))]))
    print()
    print(wl._cut_text("旺旺-SOL", "dumped -18% since we listed it"))
    print("\nwatchlist self-test OK")
