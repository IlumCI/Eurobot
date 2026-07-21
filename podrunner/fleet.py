#!/usr/bin/env python3
"""Valtgeist FLEET — multi-token paper market-making.

Runs one real Phoenix LP controller per token, concurrently, against live per-slot
prices (Jupiter). The tokens share a swarm bus — cascade alarms, a rug blacklist,
pool-crowding — and the fleet reports one aggregate USD portfolio plus a per-token
breakdown. Paper money: nothing moves, the whole book is simulated.

Why a fleet: a single low-vol pair earns almost nothing; a spread of active tokens
is where LP fees actually compound, and one token rugging is a rounding error
instead of the whole book. This is the "earn something" build.

Config (env):
  POOLS              comma-separated pool addresses; if unset, auto-discover
  FLEET_SIZE         how many pools to auto-discover (default 3)
  MIN_LIQ_USD        min pool liquidity for discovery (default 50000)
  AMOUNT_QUOTE       paper capital per pod, in that pool's quote token (default 100)
  RISK               conservative | balanced | aggressive (default balanced)
  POLL_SECONDS       poll cadence (default 3)
  ALLOW_PUMP         1 = quote pump.fun mints (default 1 for the fleet), 0 = veto them
  DRY_RUN            1 = print the fleet table instead of POSTing telemetry
"""
import asyncio
import csv
import datetime
import json
import os
import sys
import time
import urllib.request
from decimal import Decimal
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "research"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import phoenix_lp_sim as sim  # noqa: E402
from live_feed import LiveFeed  # noqa: E402
from latency import LatencyModel  # noqa: E402

LAT = LatencyModel()  # realistic execution latency, shared across the fleet

FLEET_SIZE = int(os.environ.get("FLEET_SIZE", "3"))          # target number of ACTIVE pods
AMOUNT = float(os.environ.get("AMOUNT_QUOTE", "100"))
RISK = os.environ.get("RISK", "balanced")
POLL = float(os.environ.get("POLL_SECONDS", "3"))
FEE_PCT = float(os.environ.get("POOL_FEE_PCT", "0.25"))
ALLOW_PUMP = os.environ.get("ALLOW_PUMP", "1") == "1"
DRY = os.environ.get("DRY_RUN") == "1"
# Seconds a pod waits between a close and the next open. A pod is LEGITIMATELY idle for up
# to this long every cycle, so staleness must tolerate several such waits or rotation churns
# healthy pods (the default 300s controller value at POLL=5 == 60 idle ticks — a trap).
MIN_REBALANCE = int(os.environ.get("MIN_REBALANCE", "180"))
STALE_CYCLES = int(os.environ.get("STALE_CYCLES", str(max(40, int(MIN_REBALANCE / max(POLL, 1)) * 3))))
REFILL_COOLDOWN = int(os.environ.get("REFILL_COOLDOWN", "4"))

RISK_MAP = {
    "conservative": {"kelly_fraction": "0.25", "ashes_floor_pct": "0.7"},
    "balanced": {"kelly_fraction": "0.4", "ashes_floor_pct": "0.6"},
    "aggressive": {"kelly_fraction": "0.6", "ashes_floor_pct": "0.5"},
}

UA = {"User-Agent": "valtgeist-fleet/0.1"}


def _get_json(url):
    return json.load(urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=12))


# ---------------------------------------------------------------- token selection
# "Good" for an LP market maker = where fee income beats the sigma^2/8 bleed: a graduated
# token (a real AMM pool, not a bonding curve) with real liquidity, high daily turnover
# (the fee engine), volatility in a sweet-spot band (enough to cross the band, not a rug),
# seasoned past the graduation dump. Every threshold is a HYPOTHESIS tunable by env; the
# soak report is the judge. Candidates come from GeckoTerminal's top/trending pools — real
# liquid pools ranked by volume, which is exactly the universe we want (unlike promo lists).
GT = "https://api.geckoterminal.com/api/v2/networks/solana"

SEL = {
    "liq_min":   float(os.environ.get("SEL_LIQ_MIN", "80000")),     # can exit; not one-whale-moved
    "turn_min":  float(os.environ.get("SEL_TURN_MIN", "3.0")),      # 24h volume / liquidity — the fee engine
    "vol_min":   float(os.environ.get("SEL_VOL_MIN", "3.0")),       # |priceChange.h1| %, lower edge
    "vol_max":   float(os.environ.get("SEL_VOL_MAX", "40.0")),      # upper edge (above this = rug territory)
    "chop_max":  float(os.environ.get("SEL_CHOP_MAX", "35.0")),     # |priceChange.h6| — reject strong trends
    "age_min_h": float(os.environ.get("SEL_AGE_MIN_H", "3.0")),     # hours since migration — skip dump window
    "h24_min":   float(os.environ.get("SEL_H24_MIN", "-60.0")),     # already down >60% on the day = dying
}


def _get_json_safe(url):
    try:
        return _get_json(url)
    except Exception:
        return {}


def _gt_pools():
    """Real, liquid, high-volume Solana pools (trending + top by volume), deduped."""
    seen, out = set(), []
    for url in (f"{GT}/trending_pools?page=1", f"{GT}/pools?page=1", f"{GT}/pools?page=2"):
        for p in (_get_json_safe(url) or {}).get("data", []):
            addr = (p.get("attributes") or {}).get("address")
            if addr and addr not in seen:
                seen.add(addr)
                out.append(p)
    return out


def _age_hours(iso, now):
    if not iso:
        return 0.0
    try:
        return (now - datetime.datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()) / 3600.0
    except Exception:
        return 0.0


def _metrics(p, now):
    a = p.get("attributes", {}) or {}
    liq = float(a.get("reserve_in_usd") or 0.0)
    vol = a.get("volume_usd") or {}
    ch = a.get("price_change_percentage") or {}
    v24 = float(vol.get("h24") or 0.0)
    dex = ((p.get("relationships") or {}).get("dex") or {}).get("data") or {}
    return {
        "addr": a.get("address"),
        "symbol": (a.get("name") or "?").replace(" / ", "-").replace("/", "-"),
        "dexId": dex.get("id", "?"),
        "liq": liq,
        "turnover": v24 / liq if liq > 0 else 0.0,           # daily turnover — fees per $ of capital
        "vol_h1": abs(float(ch.get("h1") or 0.0)),
        "vol_h6": abs(float(ch.get("h6") or 0.0)),
        "ch_h24": float(ch.get("h24") or 0.0),
        "age_h": _age_hours(a.get("pool_created_at"), now),
    }


def _passes(m, blacklist):
    if not m["addr"]:
        return "no-pair"
    if m["symbol"] in blacklist:
        return "blacklisted"
    if m["liq"] < SEL["liq_min"]:               # real liquidity == graduated + exitable
        return "liq<min"
    if m["age_h"] < SEL["age_min_h"]:
        return "too-fresh"
    if m["turnover"] < SEL["turn_min"]:
        return "turnover<min"
    if not (SEL["vol_min"] <= m["vol_h1"] <= SEL["vol_max"]):
        return "vol-out-of-band"
    if m["vol_h6"] > SEL["chop_max"]:
        return "trending"
    if m["ch_h24"] < SEL["h24_min"]:
        return "dying"
    return "ok"


def select_tokens(n, blacklist=frozenset()):
    """Apply the filter hypothesis, rank survivors by turnover, return the top n metric dicts."""
    now = time.time()
    passed, rejects = [], {}
    pools = _gt_pools()
    for p in pools:
        m = _metrics(p, now)
        reason = _passes(m, blacklist)
        if reason == "ok":
            passed.append(m)
        else:
            rejects[reason] = rejects.get(reason, 0) + 1
    passed.sort(key=lambda m: m["turnover"], reverse=True)
    picks = passed[:n]
    print(f"[select] {len(pools)} pools → {len(passed)} passed → top {len(picks)} by turnover", flush=True)
    if rejects:
        print("[select] rejects: " + ", ".join(f"{k} {v}" for k, v in sorted(rejects.items(), key=lambda x: -x[1])), flush=True)
    for m in picks:
        print(f"[select] {m['symbol']:16} {m['dexId']:10} liq=${m['liq']:,.0f} turn={m['turnover']:.1f} "
              f"volh1={m['vol_h1']:.1f}% age={m['age_h']:.1f}h  {m['addr']}", flush=True)
    return picks


class LivePath:
    def __init__(self, price):
        self.price = price

    def step(self, dt=1.0):
        return self.price

    def start_rug(self, steps=90):
        pass


class Pod:
    """One token: a real Phoenix controller + live feed + paper executors."""

    def __init__(self, pool_address, metrics=None):
        self.metrics = metrics or {}  # selection-time bucket (turnover, vol, age, liq)
        self.stale = 0  # consecutive cycles with no live position (perched or idle)
        self.feed = LiveFeed(pool_address)
        info = self.feed.fetch()
        self.symbol = info["symbol"]
        self.quote = self.symbol.split("-")[-1] if "-" in self.symbol else "?"
        self.quote_usd = info.get("quote_usd")
        self.price = info["price"]
        risk = RISK_MAP.get(RISK, RISK_MAP["balanced"])
        cfg = sim.PhoenixLPConfig(
            id=self.symbol, trading_pair=self.symbol, pool_address=pool_address,
            banned_ca_suffixes=[] if ALLOW_PUMP else ["pump"],
            total_amount_quote=Decimal(str(AMOUNT)),
            kelly_fraction=Decimal(risk["kelly_fraction"]),
            ashes_floor_pct=Decimal(risk["ashes_floor_pct"]),
            min_rebalance_interval=MIN_REBALANCE,
            sample_interval=int(POLL), min_samples=12, vol_window=60,
            ema_fast=6, ema_slow=12, hawkes_min_events=3,
        )
        self.clock = sim.SimClock()
        self.path = LivePath(self.price)
        self.pool = sim.SimPool(self.clock, self.path, fee_pct=FEE_PCT, mint=info["base_mint"])
        self.ctrl = sim.PhoenixLP(cfg, market_data_provider=sim.SimMDP(self.clock, self.pool))
        self.executors = []
        self.state = {}

    def apply_price(self, info):
        self.price = info["price"]
        self.path.price = info["price"]
        if info.get("quote_usd"):
            self.quote_usd = info["quote_usd"]

    async def tick(self, dt):
        self.clock.now += dt
        self.pool.data_staleness = LAT.sample_staleness()
        self.pool.rpc_latency = LAT.sample_rpc()
        self.pool.record()
        for ex in self.executors:
            ex.step()
        self.ctrl.executors_info = list(self.executors)
        await self.ctrl.update_processed_data()
        congested = float(getattr(self.ctrl, "_branching_ratio", 0.0)) >= 0.5
        for a in self.ctrl.determine_executor_actions():
            # Always apply; failure is modelled as LATER landing (retries), never a skip —
            # skipping would strand the controller's panic-flatten (it tracks it optimistically).
            lat = LAT.effective_latency(congested)
            if isinstance(a, sim.CreateExecutorAction):
                if getattr(a.executor_config, "type", None) == "order_executor":
                    self.executors.append(sim.SimOrderExecutor(a.executor_config, self.clock, self.pool, latency=lat))
                else:
                    self.executors.append(sim.SimLPExecutor(a.executor_config, self.clock, self.pool, open_latency=lat))
            elif isinstance(a, sim.StopExecutorAction):
                for ex in self.executors:
                    if ex.id == a.executor_id and hasattr(ex, "request_close"):
                        ex.request_close(lat)
        self.state = self._extract()

    def _extract(self):
        active = None
        for ex in self.executors:
            info = getattr(ex, "custom_info", {}) or {}
            if getattr(ex, "is_active", False) and "lower_price" in info:
                active = info
        fees = 0.0
        for ex in self.executors:
            info = getattr(ex, "custom_info", {}) or {}
            fees += float(info.get("quote_fee", 0.0)) + float(info.get("base_fee", 0.0)) * self.price
        equity = float(self.ctrl.equity())
        pos = "idle"
        if active:
            side = "bid" if active.get("initial_quote_amount", 0) > 0 else "ask"
            st = active.get("state")
            pos = {"OPENING": "opening", "CLOSING": "closing",
                   "IN_RANGE": f"{side}_in", "OUT_OF_RANGE": f"{side}_out"}.get(st, "opening")
        return {
            "runtime_state": self.ctrl.state, "position": pos, "price": self.price,
            "equity": equity, "pnl": equity - float(self.ctrl.config.total_amount_quote),
            "fees_earned": fees, "hawkes_n": float(self.ctrl._branching_ratio),
            "quote_usd": self.quote_usd, "quote": self.quote,
        }


def usd(v, q):
    return v * q if (q and v is not None) else None


class Swarm:
    """Shared intelligence across the fleet — never shared capital."""

    def __init__(self):
        self.blacklist = set()   # permanent: blew up / refused
        self.cooldown = {}       # symbol -> cycle when re-eligible (stale, but not necessarily bad)
        self.alarms = 0
        self.vetoes = 0
        self.retired_pnl = 0.0    # banked USD pnl from retired tokens — keeps the total honest
        self.retired_fees = 0.0
        self.retired = 0
        self.last_refill = -10 ** 9  # allow the first refill immediately

    def update(self, pods):
        self.alarms = sum(1 for p in pods
                          if p.state.get("runtime_state") == "PERCHED" or p.state.get("hawkes_n", 0) >= 0.70)
        self.vetoes = sum(1 for p in pods if p.state.get("runtime_state") == "VETOED")

    def excluded(self, now):
        """Symbols the selector must skip: permanently blacklisted + still on cooldown."""
        return self.blacklist | {s for s, c in self.cooldown.items() if c > now}

    def retire(self, pod, reason, now, permanent):
        """Bank the retired pod's realized result (so churn can't hide losses). Blown/refused
        tokens are blacklisted forever; merely-stale ones get a cooldown so the universe of
        candidates isn't exhausted over a long soak."""
        s = pod.state
        q = s.get("quote_usd")
        self.retired_pnl += usd(s.get("pnl"), q) or 0.0
        self.retired_fees += usd(s.get("fees_earned"), q) or 0.0
        self.retired += 1
        if permanent:
            self.blacklist.add(pod.symbol)
        else:
            self.cooldown[pod.symbol] = now + STALE_CYCLES * 2
        print(f"[fleet] retire {pod.symbol} ({reason}) pnl={s.get('pnl',0):+.4f} {pod.quote} "
              f"fees={s.get('fees_earned',0):.5f}", flush=True)


def emit(pods, swarm, t):
    live_eq = sum(x for x in (usd(p.state.get("equity"), p.state.get("quote_usd")) for p in pods) if x) or 0.0
    live_pnl = sum(x for x in (usd(p.state.get("pnl"), p.state.get("quote_usd")) for p in pods) if x) or 0.0
    live_fees = sum(x for x in (usd(p.state.get("fees_earned"), p.state.get("quote_usd")) for p in pods) if x) or 0.0
    # book total = live pods + everything already banked from retirements (honest cumulative)
    port_eq = live_eq + swarm.retired_pnl
    port_pnl = live_pnl + swarm.retired_pnl
    port_fees = live_fees + swarm.retired_fees
    print(f"\n[fleet t+{t:>4}s] PORTFOLIO ${port_eq:,.2f}  pnl {port_pnl:+.4f}$  fees {port_fees:.4f}$  "
          f"| {len(pods)} live · retired {swarm.retired} · perched {swarm.alarms} · "
          f"vetoed {swarm.vetoes} · blacklist {len(swarm.blacklist)} · tx-drops {LAT.drops}",
          flush=True)
    for p in pods:
        s = p.state
        print(f"    {p.symbol:18} {s.get('runtime_state',''):<8} {s.get('position',''):<8} "
              f"eq={s.get('equity',0):.4f} {p.quote:<5} pnl={s.get('pnl',0):+.4f} "
              f"fees={s.get('fees_earned',0):.5f} n={s.get('hawkes_n',0):.2f}", flush=True)


SOAK_CSV = os.environ.get("SOAK_CSV")
CSV_COLS = ["ts", "t", "symbol", "dexId", "liq", "turnover", "vol_h1", "vol_h6", "age_h",
            "state", "position", "quote", "quote_usd", "eq_q", "pnl_q", "fees_q",
            "usd_eq", "usd_pnl", "usd_fees", "hawkes_n", "tx_drops"]


def log_csv(path, pods, t):
    """Append one row per pod per cycle, tagged with its selection bucket — the soak's
    raw material. soak_report.py turns this into a per-bucket verdict."""
    new = not os.path.exists(path)
    with open(path, "a", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(CSV_COLS)
        ts = time.time()
        for p in pods:
            s, m = p.state, p.metrics
            q = s.get("quote_usd")
            w.writerow([f"{ts:.0f}", t, p.symbol, m.get("dexId", ""), f"{m.get('liq',0):.0f}",
                        f"{m.get('turnover',0):.3f}", f"{m.get('vol_h1',0):.2f}", f"{m.get('vol_h6',0):.2f}",
                        f"{m.get('age_h',0):.2f}", s.get("runtime_state", ""), s.get("position", ""),
                        p.quote, f"{q:.6f}" if q else "", f"{s.get('equity',0):.6f}",
                        f"{s.get('pnl',0):.6f}", f"{s.get('fees_earned',0):.6f}",
                        f"{usd(s.get('equity'),q) or 0:.4f}", f"{usd(s.get('pnl'),q) or 0:.4f}",
                        f"{usd(s.get('fees_earned'),q) or 0:.4f}", f"{s.get('hawkes_n',0):.3f}", LAT.drops])


async def rotate(pods, swarm, target, auto, now):
    """Retire dead/stale pods (bank their result), then refill to target from fresh
    selection. This is what makes the fleet a rolling book instead of a decaying snapshot."""
    # mark staleness — but warm-up (HATCHING) is legitimately idle, so it doesn't count
    for p in pods:
        if p.state.get("runtime_state") == "HATCHING":
            p.stale = 0
            continue
        active = p.state.get("position") not in ("idle", "", None)
        p.stale = 0 if active else p.stale + 1
    # retire the dead: refused at start / hit the equity floor (permanent), or stopped
    # trading far longer than a rebalance interval (cooldown — may be fine again later)
    for p in list(pods):
        st = p.state.get("runtime_state")
        if st == "VETOED":
            swarm.retire(p, "VETOED", now, permanent=True)
            pods.remove(p)
        elif st == "ASHES":
            swarm.retire(p, "ASHES", now, permanent=True)
            pods.remove(p)
        elif p.stale >= STALE_CYCLES:
            swarm.retire(p, f"stale {p.stale}c", now, permanent=False)
            pods.remove(p)
    # refill to target — rate-limited (select_tokens hits the network); pinned POOLS just shrink
    if not auto or len(pods) >= target or (now - swarm.last_refill) < REFILL_COOLDOWN:
        return
    swarm.last_refill = now
    held = {p.symbol for p in pods}
    picks = await asyncio.to_thread(select_tokens, target - len(pods) + 3, swarm.excluded(now) | held)
    for m in picks:
        if len(pods) >= target:
            break
        if m["symbol"] in held:
            continue
        try:
            newpod = await asyncio.to_thread(Pod, m["addr"], m)
            pods.append(newpod)
            held.add(newpod.symbol)
            print(f"[fleet] add {newpod.symbol}", flush=True)
        except Exception as e:
            print(f"[fleet] skip {m['symbol']} ({e})", flush=True)


async def main():
    explicit = [x for x in (os.environ.get("POOLS", "").split(",")) if x]
    if explicit:
        picks = [{"addr": a} for a in explicit]
    else:
        picks = select_tokens(FLEET_SIZE)
    if not picks:
        sys.exit("[fleet] no pools passed the filter (loosen SEL_* or set POOLS=addr1,addr2).")

    pods = []
    for m in picks:
        try:
            pods.append(Pod(m["addr"], m))
            print(f"[fleet] armed {pods[-1].symbol}", flush=True)
        except Exception as e:
            print(f"[fleet] skip {m['addr'][:8]}… ({e})", flush=True)
    if not pods:
        sys.exit("[fleet] every pod failed to arm.")
    if SOAK_CSV:
        print(f"[fleet] soak logging → {SOAK_CSV}", flush=True)

    swarm = Swarm()
    auto = not explicit
    target = len(pods)  # hold the fleet at the size it successfully armed
    n = 0
    while True:
        if pods:
            infos = await asyncio.gather(*[asyncio.to_thread(p.feed.fetch) for p in pods], return_exceptions=True)
            for p, info in zip(pods, infos):
                if not isinstance(info, Exception):
                    p.apply_price(info)
            for p in pods:
                await p.tick(POLL)
        swarm.update(pods)
        n += 1
        t = int(n * POLL)
        if SOAK_CSV:
            log_csv(SOAK_CSV, pods, t)
        if DRY:
            emit(pods, swarm, t)
        # rotation runs every cycle: staleness+retire are cheap; the refill self-rate-limits
        await rotate(pods, swarm, target, auto, n)
        await asyncio.sleep(POLL)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[fleet] stopped")
