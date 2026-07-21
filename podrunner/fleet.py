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
import json
import math
import os
import sys
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

FLEET_SIZE = int(os.environ.get("FLEET_SIZE", "3"))
MIN_LIQ = float(os.environ.get("MIN_LIQ_USD", "50000"))
AMOUNT = float(os.environ.get("AMOUNT_QUOTE", "100"))
RISK = os.environ.get("RISK", "balanced")
POLL = float(os.environ.get("POLL_SECONDS", "3"))
FEE_PCT = float(os.environ.get("POOL_FEE_PCT", "0.25"))
ALLOW_PUMP = os.environ.get("ALLOW_PUMP", "1") == "1"
DRY = os.environ.get("DRY_RUN") == "1"

RISK_MAP = {
    "conservative": {"kelly_fraction": "0.25", "ashes_floor_pct": "0.7"},
    "balanced": {"kelly_fraction": "0.4", "ashes_floor_pct": "0.6"},
    "aggressive": {"kelly_fraction": "0.6", "ashes_floor_pct": "0.5"},
}

UA = {"User-Agent": "valtgeist-fleet/0.1"}


def _get_json(url):
    return json.load(urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=12))


def discover_pools(n, min_liq):
    """Pick the n most-active Solana pools from DexScreener's boosted list, ranked by
    short-term move x liquidity. This is the fleet's auto-selector."""
    rows = []
    try:
        boosts = _get_json("https://api.dexscreener.com/token-boosts/top/v1")
    except Exception as e:
        print(f"[fleet] discovery failed ({e})", flush=True)
        return []
    for b in [x for x in boosts if x.get("chainId") == "solana"][:25]:
        try:
            pairs = _get_json(f"https://api.dexscreener.com/token-pairs/v1/solana/{b['tokenAddress']}")
        except Exception:
            continue
        if not pairs:
            continue
        p = max(pairs, key=lambda x: (x.get("liquidity") or {}).get("usd") or 0)
        liq = (p.get("liquidity") or {}).get("usd") or 0
        m5 = abs((p.get("priceChange") or {}).get("m5") or 0)
        if liq >= min_liq and p.get("pairAddress"):
            rows.append((m5 * math.log10(liq + 10), p["pairAddress"],
                         f"{p.get('baseToken',{}).get('symbol')}-{p.get('quoteToken',{}).get('symbol')}"))
    rows.sort(reverse=True)
    picks = rows[:n]
    for _, addr, sym in picks:
        print(f"[fleet] selected {sym:18} {addr}", flush=True)
    return [addr for _, addr, _ in picks]


class LivePath:
    def __init__(self, price):
        self.price = price

    def step(self, dt=1.0):
        return self.price

    def start_rug(self, steps=90):
        pass


class Pod:
    """One token: a real Phoenix controller + live feed + paper executors."""

    def __init__(self, pool_address):
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
            if isinstance(a, sim.CreateExecutorAction):
                if LAT.dropped(congested):
                    continue  # tx failed to land; re-issued next tick
                lat = LAT.action_latency(congested)
                if getattr(a.executor_config, "type", None) == "order_executor":
                    self.executors.append(sim.SimOrderExecutor(a.executor_config, self.clock, self.pool, latency=lat))
                else:
                    self.executors.append(sim.SimLPExecutor(a.executor_config, self.clock, self.pool, open_latency=lat))
            elif isinstance(a, sim.StopExecutorAction):
                if LAT.dropped(congested):
                    continue  # panic-close failed under congestion — the real risk, modelled
                lat = LAT.action_latency(congested)
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


class Swarm:
    """Shared intelligence across the fleet — never shared capital."""

    def __init__(self):
        self.blacklist = set()
        self.alarms = 0
        self.vetoes = 0

    def update(self, pods):
        self.alarms = sum(1 for p in pods
                          if p.state.get("runtime_state") == "PERCHED" or p.state.get("hawkes_n", 0) >= 0.70)
        self.vetoes = sum(1 for p in pods if p.state.get("runtime_state") == "VETOED")
        for p in pods:
            if p.state.get("runtime_state") == "ASHES":
                self.blacklist.add(p.symbol)


def usd(v, q):
    return v * q if (q and v is not None) else None


def emit(pods, swarm, t):
    port_eq = sum(x for x in (usd(p.state.get("equity"), p.state.get("quote_usd")) for p in pods) if x) or 0.0
    port_pnl = sum(x for x in (usd(p.state.get("pnl"), p.state.get("quote_usd")) for p in pods) if x) or 0.0
    port_fees = sum(x for x in (usd(p.state.get("fees_earned"), p.state.get("quote_usd")) for p in pods) if x) or 0.0
    print(f"\n[fleet t+{t:>4}s] PORTFOLIO ${port_eq:,.2f}  pnl {port_pnl:+.4f}$  fees {port_fees:.4f}$  "
          f"| {len(pods)} pods · perched/alarm {swarm.alarms} · vetoed {swarm.vetoes} · "
          f"blacklist {len(swarm.blacklist)} · tx-drops {LAT.drops}",
          flush=True)
    for p in pods:
        s = p.state
        print(f"    {p.symbol:18} {s.get('runtime_state',''):<8} {s.get('position',''):<8} "
              f"eq={s.get('equity',0):.4f} {p.quote:<5} pnl={s.get('pnl',0):+.4f} "
              f"fees={s.get('fees_earned',0):.5f} n={s.get('hawkes_n',0):.2f}", flush=True)


async def main():
    pools = [x for x in (os.environ.get("POOLS", "").split(",")) if x] or discover_pools(FLEET_SIZE, MIN_LIQ)
    if not pools:
        sys.exit("[fleet] no pools (set POOLS=addr1,addr2 or check discovery).")

    pods = []
    for addr in pools:
        try:
            pods.append(Pod(addr))
            print(f"[fleet] armed {pods[-1].symbol}", flush=True)
        except Exception as e:
            print(f"[fleet] skip {addr[:8]}… ({e})", flush=True)
    if not pods:
        sys.exit("[fleet] every pod failed to arm.")

    swarm = Swarm()
    n = 0
    while True:
        infos = await asyncio.gather(*[asyncio.to_thread(p.feed.fetch) for p in pods], return_exceptions=True)
        for p, info in zip(pods, infos):
            if not isinstance(info, Exception):
                p.apply_price(info)
        for p in pods:
            await p.tick(POLL)
        swarm.update(pods)
        n += 1
        if DRY:
            emit(pods, swarm, int(n * POLL))
        await asyncio.sleep(POLL)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[fleet] stopped")
