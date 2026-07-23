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
  RISK               conservative | balanced | aggressive | max (default balanced)
                     max = hit-and-run: short liquidity windows on the most volatile/active
                     tokens, trailing-stop + dwell-TTL exits, fastest cadence, pump forced on.
  POLL_SECONDS       poll cadence (default 3; max mode 2)
  MAX_DWELL          max seconds a pod holds one token (default 900 in max mode, else off)
  TRAIL_STOP_PCT     exit when pnl drops this % of stake below its peak (default 1.5 in max)
  ALLOW_PUMP         1 (default) = include pump.fun tokens + STRICT selection hypothesis (turnover/
                     vol-band/age). Blocking pump strips ~96% of the live Solana market, so allow is
                     the default. 0 = BLOCK pump.fun and LIFT the strict filter — pick the most
                     volatile/active non-pump tokens instead (see select_tokens for the two modes).
  DRY_RUN            1 = print the fleet table instead of POSTing telemetry
"""
import asyncio
import csv
import datetime
import json
import os
import random
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
from live_feed import LiveFeed, jupiter_usd_prices  # noqa: E402
from latency import LatencyModel  # noqa: E402
from flow_metrics import FlowMeter  # noqa: E402

LAT = LatencyModel()  # realistic execution latency, shared across the fleet

FLEET_SIZE = int(os.environ.get("FLEET_SIZE", "3"))          # target number of ACTIVE pods
AMOUNT = float(os.environ.get("AMOUNT_QUOTE", "100"))
RISK = os.environ.get("RISK", "balanced")   # conservative | balanced | aggressive | max
# RISK=max — hit-and-run mode: short liquidity windows on the most volatile/active tokens,
# trailing-stop + dwell-TTL exits, fastest cadence the public APIs allow. The research basis:
# winning JIT LPs earn ~94% of revenue from directional timing, not passive fee collection —
# max mode operationalizes "be in only while flow is favorable, exit before it turns".
POLL = float(os.environ.get("POLL_SECONDS", "2" if RISK == "max" else "3"))
FEE_PCT = float(os.environ.get("POOL_FEE_PCT", "0.25"))
# max unblocks everything (mostly): pump.fun is forced on — that's where the volatility lives.
ALLOW_PUMP = os.environ.get("ALLOW_PUMP", "1") == "1" or RISK == "max"  # default ALLOWS pump: blocking empirically strips ~96% of the Solana market (dead). ALLOW_PUMP=0 = non-pump/volatile mode.
DRY = os.environ.get("DRY_RUN") == "1"
# Hit-and-run exits (defaults ON only for RISK=max; set env explicitly to use them elsewhere):
#   MAX_DWELL       seconds a pod may hold one token before it's rotated out regardless (the
#                   "run" in hit-and-run — never overstay a window that was only ever short)
#   TRAIL_STOP_PCT  exit when pnl falls this % of stake below its peak. From peak 0 this is a
#                   plain stop-loss, so a token that dives on entry is cut immediately.
MAX_DWELL = float(os.environ.get("MAX_DWELL", "900" if RISK == "max" else "0"))
TRAIL_STOP_PCT = float(os.environ.get("TRAIL_STOP_PCT", "1.5" if RISK == "max" else "0"))
# Live websocket vault feed (free, raced across public endpoints): sub-second prices for
# CPMM pools + REAL per-trade flow -> VPIN toxicity + true Hawkes arrivals. WS_FEED=0 disables.
WS_FEED = os.environ.get("WS_FEED", "1") == "1"
WS_MAX_AGE_MS = float(os.environ.get("WS_MAX_AGE_MS", "3000"))  # older ws price -> fall back to Jupiter
# Toxic-flow exit: when a pod's VPIN crosses this AND the flow is sell-sided, exit to cash
# (cooldown). The research basis: VPIN leads price jumps — this is the earliest rug warning
# we have, firing BEFORE the price cascade the Hawkes detector needs. TOX_VPIN=0 disables.
TOX_VPIN = float(os.environ.get("TOX_VPIN", "0.75" if RISK == "max" else "0.85"))
WSF = None  # ws vault feed singleton, started in main() when WS_FEED is on
CHART_GUI = os.environ.get("CHART_GUI") == "1"  # 1 = also pop a live matplotlib window (needs a backend)
GUI = None  # set in main() when CHART_GUI is on; emit() feeds it the curve
# Real-time risk/flow alerts -> Telegram (the free, no-custody product). Edge-triggered on the
# cascade/VPIN/halt signals the fleet already computes; posts to a channel or just the console.
ALERTS = os.environ.get("ALERTS") == "1"
ALERTBOOK = None  # set in main() when ALERTS is on
# Seconds a pod waits between a close and the next open. max mode reprices near-continuously:
# sitting on a stale quote is exactly the LVR bleed, and Solana gas is cheap enough to reprice.
MIN_REBALANCE = int(os.environ.get("MIN_REBALANCE", "20" if RISK == "max" else "180"))
# Staleness is measured as consecutive FEE-LESS cycles (see rotate()): "how long a pod may earn
# nothing before it's eligible for rotation." ~One rebalance-interval of drought is a fair grace
# period; the replacement-first guard in rotate() only actually swaps a dead pod when a better
# candidate exists, so this can be responsive without churning a genuinely quiet-but-fine market.
STALE_CYCLES = int(os.environ.get("STALE_CYCLES", str(max(24, int(MIN_REBALANCE / max(POLL, 1))))))
# Cycles between candidate re-scans. max polls fast (2s), so a higher cycle count here keeps the
# GeckoTerminal call rate (~3 calls/scan) safely under its rate limit while still re-scanning ~15s.
REFILL_COOLDOWN = int(os.environ.get("REFILL_COOLDOWN", "8" if RISK == "max" else "4"))
# Cross-run memory of tokens that blew up / cascaded, so a restart doesn't re-pick the same
# failures. Symbols only (addresses churn); good enough to steer away from repeat offenders.
BLACKLIST_FILE = os.environ.get("BLACKLIST_FILE", str(Path(__file__).resolve().parent / "fleet_blacklist.json"))


def load_blacklist():
    try:
        with open(BLACKLIST_FILE) as f:
            return set(json.load(f))
    except Exception:
        return set()


def save_blacklist(bl):
    try:
        with open(BLACKLIST_FILE, "w") as f:
            json.dump(sorted(bl), f)
    except Exception:
        pass

RISK_MAP = {
    "conservative": {"kelly_fraction": "0.25", "ashes_floor_pct": "0.7"},
    "balanced": {"kelly_fraction": "0.4", "ashes_floor_pct": "0.6"},
    "aggressive": {"kelly_fraction": "0.6", "ashes_floor_pct": "0.5"},
    # max: aggressive deploy per pod, but the REAL protection is the hit-and-run exits (trailing
    # stop fires at ~1.5% of stake, far above the ashes floor) + many small pods. Run max with a
    # bigger FLEET_SIZE and a smaller AMOUNT_QUOTE — diversify the jump risk, don't concentrate it.
    "max": {"kelly_fraction": "0.7", "ashes_floor_pct": "0.45"},
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

# RISK also bends SELECTION strictness (not just the controller's Kelly sizing). Multipliers on the
# env baseline: conservative = stricter (fewer, "more perfect" tokens); aggressive = looser (more
# candidates, fills more fleet slots). The guaranteed-pick floor in select_tokens means even the
# strictest risk never returns 0 when an eligible token exists.
SEL_RISK = {
    "conservative": {"liq": 1.75, "turn": 1.6, "vol_lo": 1.6, "vol_hi": 0.65, "chop": 0.70, "age": 1.6, "h24": 0.60},
    "balanced":     {"liq": 1.00, "turn": 1.0, "vol_lo": 1.0, "vol_hi": 1.00, "chop": 1.00, "age": 1.0, "h24": 1.00},
    "aggressive":   {"liq": 0.55, "turn": 0.5, "vol_lo": 0.4, "vol_hi": 1.50, "chop": 1.50, "age": 0.4, "h24": 1.40},
    # max: unblock (mostly) — no vol ceiling worth speaking of (120% h1), tokens as young as
    # ~18min, deep drawdowns allowed. What stays: a liquidity floor (~$32k — the RUN half of
    # hit-and-run needs an exit door) and the blacklist. Danger is the point; illiquidity isn't.
    "max":          {"liq": 0.40, "turn": 0.3, "vol_lo": 0.0, "vol_hi": 3.00, "chop": 3.00, "age": 0.1, "h24": 1.50},
}
_RS = SEL_RISK.get(RISK, SEL_RISK["balanced"])

SEL = {
    "liq_min":   float(os.environ.get("SEL_LIQ_MIN", "80000")) * _RS["liq"],    # can exit; not one-whale-moved
    "turn_min":  float(os.environ.get("SEL_TURN_MIN", "3.0")) * _RS["turn"],    # 24h volume / liquidity — the fee engine
    "vol_min":   float(os.environ.get("SEL_VOL_MIN", "3.0")) * _RS["vol_lo"],   # |priceChange.h1| %, lower edge
    "vol_max":   float(os.environ.get("SEL_VOL_MAX", "40.0")) * _RS["vol_hi"],  # upper edge (above this = rug territory)
    "chop_max":  float(os.environ.get("SEL_CHOP_MAX", "35.0")) * _RS["chop"],   # |priceChange.h6| — reject strong trends
    "age_min_h": float(os.environ.get("SEL_AGE_MIN_H", "3.0")) * _RS["age"],    # hours since migration — skip dump window
    "h24_min":   float(os.environ.get("SEL_H24_MIN", "-60.0")) * _RS["h24"],    # already down this % on the day = dying
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
    rel = p.get("relationships") or {}
    dex = (rel.get("dex") or {}).get("data") or {}
    base_id = ((rel.get("base_token") or {}).get("data") or {}).get("id", "")  # "solana_<mint>"
    return {
        "addr": a.get("address"),
        "symbol": (a.get("name") or "?").replace(" / ", "-").replace("/", "-"),
        "dexId": dex.get("id", "?"),
        "base_mint": base_id.split("_", 1)[1] if "_" in base_id else base_id,
        "liq": liq,
        "turnover": v24 / liq if liq > 0 else 0.0,           # daily turnover — fees per $ of capital
        "vol_h1": abs(float(ch.get("h1") or 0.0)),
        "vol_h6": abs(float(ch.get("h6") or 0.0)),
        "ch_h24": float(ch.get("h24") or 0.0),
        "age_h": _age_hours(a.get("pool_created_at"), now),
    }


def _is_pump(m):
    """A pump.fun token: mint ends in 'pump' (the pump.fun mint convention — the same suffix the
    controller vetoes) or it trades on pump.fun's own AMM (pumpswap)."""
    return (m["base_mint"] or "").endswith("pump") or "pump" in (m["dexId"] or "").lower()


def _passes(m, blacklist):
    """STRICT hypothesis (pump allowed): the graduated-token bet — real liquidity, high turnover,
    a volatility sweet-spot band, seasoned past the graduation dump."""
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


def _passes_open(m, blacklist):
    """LIGHT-TOUCH gate (pump BLOCKED): the strict hypothesis is lifted, so we only exclude what
    makes a token un-market-makeable — pump.fun itself, no/low liquidity (can't exit), a
    blacklisted name, or a token that already collapsed today. Ranking then picks the liveliest."""
    if not m["addr"]:
        return "no-pair"
    if _is_pump(m):
        return "pump"
    if m["symbol"] in blacklist:
        return "blacklisted"
    if m["liq"] < SEL["liq_min"]:      # still need enough liquidity to enter/exit safely
        return "liq<min"
    if m["ch_h24"] < SEL["h24_min"]:   # already down hard on the day = dead, skip
        return "dying"
    return "ok"


def _shortfall(m):
    """How far a token is from the ideal criteria (0 == passes; higher == further), summed as
    normalized violations. Used only to rank NEAR-MISSES when too few tokens pass, so the closest
    one to meeting the bar is chosen for the guaranteed-pick fallback."""
    s = 0.0
    if m["liq"] < SEL["liq_min"]:
        s += (SEL["liq_min"] - m["liq"]) / max(SEL["liq_min"], 1e-9)
    if m["turnover"] < SEL["turn_min"]:
        s += (SEL["turn_min"] - m["turnover"]) / max(SEL["turn_min"], 1e-9)
    if m["vol_h1"] < SEL["vol_min"]:
        s += (SEL["vol_min"] - m["vol_h1"]) / max(SEL["vol_min"], 1e-9)
    elif m["vol_h1"] > SEL["vol_max"]:
        s += (m["vol_h1"] - SEL["vol_max"]) / max(SEL["vol_max"], 1e-9)
    if m["vol_h6"] > SEL["chop_max"]:
        s += (m["vol_h6"] - SEL["chop_max"]) / max(SEL["chop_max"], 1e-9)
    if m["age_h"] < SEL["age_min_h"]:
        s += (SEL["age_min_h"] - m["age_h"]) / max(SEL["age_min_h"], 1e-9)
    if m["ch_h24"] < SEL["h24_min"]:
        s += (SEL["h24_min"] - m["ch_h24"]) / max(abs(SEL["h24_min"]), 1e-9)
    return s


# How many fleet slots each risk appetite insists on filling, even if it means topping up with
# near-misses: conservative fills only 1 (stay picky, prefer empty slots to marginal tokens),
# aggressive fills all n (fill every slot), balanced ~half. Always >= 1 whenever a token is eligible.
def _risk_floor(n):
    return {"conservative": 1, "balanced": max(1, n // 2), "aggressive": n, "max": n}.get(RISK, max(1, n // 2))


def select_tokens(n, blacklist=frozenset()):
    """Pick n tokens. TWO MODES, keyed off ALLOW_PUMP:
      * ALLOW_PUMP=1 — STRICT hypothesis (_passes): graduated pump.fun tokens with turnover, a
        volatility sweet-spot, seasoned past the dump. Ranked by turnover (the fee engine).
      * ALLOW_PUMP=0 — pump.fun BLOCKED, strict hypothesis LIFTED (_passes_open): keep any liquid,
        non-pump, non-dying token and rank by ACTIVITY x VOLATILITY (turnover * |1h move|).
    RISK bends the thresholds (SEL_RISK) AND the fill floor (_risk_floor). If too few tokens pass,
    top up with the CLOSEST near-misses (_shortfall) so the fleet is never left short a slot it was
    told to fill — but pump.fun and blacklisted names stay HARD-excluded even as fallbacks, so the
    guarantee is ">= floor among ELIGIBLE tokens", never "pick junk we explicitly banned"."""
    now = time.time()
    passed, failed, rejects = [], [], {}
    pools = _gt_pools()
    gate = _passes if ALLOW_PUMP else _passes_open
    for p in pools:
        m = _metrics(p, now)
        # Hard exclusions — never overridden, not even by the guaranteed-pick fallback.
        if not m["addr"]:
            rejects["no-pair"] = rejects.get("no-pair", 0) + 1
            continue
        if not ALLOW_PUMP and _is_pump(m):
            rejects["pump"] = rejects.get("pump", 0) + 1
            continue
        if m["symbol"] in blacklist:
            rejects["blacklisted"] = rejects.get("blacklisted", 0) + 1
            continue
        reason = gate(m, blacklist)
        if reason == "ok":
            passed.append(m)
        else:
            failed.append(m)
            rejects[reason] = rejects.get(reason, 0) + 1
    # Rank (strict: turnover; blocked: activity x volatility), then RANDOM-SAMPLE from the top band
    # so different runs try different names instead of re-picking the same ones (and re-picking failures).
    key = (lambda m: m["turnover"]) if ALLOW_PUMP else (lambda m: m["turnover"] * max(m["vol_h1"], 0.1))
    if RISK == "max":
        # fee velocity — the most ACTIVE and most VOLATILE simultaneously; that's where the
        # short-window fee income is, and the hit-and-run exits are the counterweight.
        key = lambda m: m["turnover"] * max(m["vol_h1"], 0.1)
    passed.sort(key=key, reverse=True)
    # max samples from a tighter top band: hit-and-run wants the best windows RIGHT NOW,
    # not diversity across the top-3n — churn (dwell TTL) provides the variety instead.
    pool = passed[:max(n * (2 if RISK == "max" else 3), n)]
    picks = random.sample(pool, min(n, len(pool)))
    # Guaranteed fill: if fewer than the risk floor passed, top up with the closest near-misses —
    # but only TRADEABLE ones (>= half the liq floor). The fallback may bend quality bars; it may
    # not bend exitability — an illiquid pool breaks the exit and usually won't even arm.
    floor = min(_risk_floor(n), len(passed) + len(failed))
    n_fallback = 0
    if len(picks) < floor:
        for m in sorted((m for m in failed if m["liq"] >= SEL["liq_min"] * 0.5), key=_shortfall):
            if len(picks) >= floor:
                break
            m["_fallback"] = True
            picks.append(m)
            n_fallback += 1
    # The >=1 promise is absolute: if still empty-handed, take the single closest candidate.
    if not picks and failed:
        m = min(failed, key=_shortfall)
        m["_fallback"] = True
        picks.append(m)
        n_fallback += 1
    mode = "pump-ok/strict" if ALLOW_PUMP else "no-pump/volatile"
    fb = f" (+{n_fallback} near-miss to fill floor {floor})" if n_fallback else ""
    print(f"[select:{mode}/{RISK}] {len(pools)} pools → {len(passed)} passed → {len(picks)} picked{fb}", flush=True)
    if rejects:
        print("[select] rejects: " + ", ".join(f"{k} {v}" for k, v in sorted(rejects.items(), key=lambda x: -x[1])), flush=True)
    for m in picks:
        tag = "  ~near-miss" if m.get("_fallback") else ""
        print(f"[select] {m['symbol']:16} {m['dexId']:10} liq=${m['liq']:,.0f} turn={m['turnover']:.1f} "
              f"volh1={m['vol_h1']:.1f}% age={m['age_h']:.1f}h  {m['addr']}{tag}", flush=True)
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
        self.stale = 0  # consecutive cycles earning no new fees (dead weight -> rotate)
        self.fee_mark = 0.0  # last cumulative fee reading, to detect fee progress
        self.born = time.time()  # when this pod armed — drives the max-mode dwell TTL
        self.peak_pnl = 0.0      # best pnl seen (quote units) — drives the trailing stop
        self.flow = FlowMeter()  # VPIN / imbalance / arrival rate from real trades
        self.ws = False          # registered on the ws vault feed?
        self.ws_price_ok = False # CPMM (vault-ratio price valid) vs CLMM (flow-only)
        self.price_src = "jup"
        self.feed = LiveFeed(pool_address)
        info = self.feed.fetch()
        self.symbol = info["symbol"]
        self.quote = self.symbol.split("-")[-1] if "-" in self.symbol else "?"
        self.base_mint = info.get("base_mint")
        self.quote_mint = info.get("quote_mint")
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
        # Register on the live vault feed: sub-second price (CPMM) + real trade flow (all venues).
        # Failure is never fatal — the pod just stays on Jupiter polling.
        if WSF is not None:
            try:
                from vault_discovery import discover_vaults
                v = discover_vaults(pool_address, self.base_mint, self.quote_mint)
                WSF.watch_pool(self.symbol, v)
                self.ws, self.ws_price_ok = True, v["price_capable"]
            except Exception as e:
                print(f"[fleet] {self.symbol}: no ws feed ({e}); polling only", flush=True)

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
        unreal = 0.0  # mark-to-market of OPEN positions (fees earned minus impermanent loss)
        px = self.price
        for ex in self.executors:
            info = getattr(ex, "custom_info", {}) or {}
            if getattr(ex, "is_active", False) and "lower_price" in info:
                active = info
                # Same net-value formula the controller banks on close (_settle_closed_executor),
                # applied to the still-open position so equity moves in real time instead of only
                # jumping when a position finally settles. No double-count: closed positions are
                # already in equity() via _hold and are is_active=False here.
                base_net = (float(info.get("base_amount", 0)) + float(info.get("base_fee", 0))
                            - float(info.get("initial_base_amount", 0)))
                quote_net = (float(info.get("quote_amount", 0)) + float(info.get("quote_fee", 0))
                             - float(info.get("initial_quote_amount", 0)))
                unreal += base_net * px + quote_net
        fees = 0.0
        for ex in self.executors:
            info = getattr(ex, "custom_info", {}) or {}
            fees += float(info.get("quote_fee", 0.0)) + float(info.get("base_fee", 0.0)) * self.price
        equity = float(self.ctrl.equity()) + unreal  # realized + open-position mark-to-market
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
        self.baseline = None      # opening portfolio value (USD), fixed at first emit
        self.curve = []           # rotation-neutral portfolio value over time, for the chart

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
            save_blacklist(self.blacklist)  # remember it across runs
        else:
            self.cooldown[pod.symbol] = now + STALE_CYCLES * 2
        if WSF is not None and pod.ws:
            try:
                WSF.unwatch_pool(pod.symbol)
            except Exception:
                pass
        print(f"[fleet] retire {pod.symbol} ({reason}) pnl={s.get('pnl',0):+.4f} {pod.quote} "
              f"fees={s.get('fees_earned',0):.5f}", flush=True)


# ---------------------------------------------------------------- terminal chart
# A dependency-free braille line chart (2x4 dots per cell) so the soak shows a real
# portfolio curve in the terminal, not just a scrolling number. stdlib only.
_A = {"g": "\033[32m", "r": "\033[31m", "d": "\033[2m", "b": "\033[1m", "x": "\033[0m"}
_BR = 0x2800
_DOT = {(0, 0): 0x01, (0, 1): 0x02, (0, 2): 0x04, (0, 3): 0x40,
        (1, 0): 0x08, (1, 1): 0x10, (1, 2): 0x20, (1, 3): 0x80}


def _chart(vals, color, width=56, height=6):
    """Braille line chart of the whole run (resampled to fit), with a $ axis gutter.
    O(width) regardless of history length, so a multi-day curve stays cheap to draw."""
    cols, rows = width * 2, height * 4
    n = len(vals)
    pts = [vals[int(i * (n - 1) / (cols - 1))] for i in range(cols)]
    lo, hi = min(pts), max(pts)
    span = (hi - lo) or 1e-9
    ys = [int(round((v - lo) / span * (rows - 1))) for v in pts]
    grid = [[0] * width for _ in range(height)]

    def dot(cx, ry):
        top = (rows - 1) - ry                      # braille is top-origin; our y is bottom-origin
        grid[top // 4][cx // 2] |= _DOT[(cx % 2, top % 4)]

    for cx in range(cols):
        y = ys[cx]
        if cx:                                     # draw the segment from the previous point so it reads as a line
            step = 1 if y >= ys[cx - 1] else -1
            for yy in range(ys[cx - 1], y, step):
                dot(cx, yy)
        dot(cx, y)

    lab = [f"${hi:,.2f}"] + [""] * (height - 2) + [f"${lo:,.2f}"]
    w = max(len(s) for s in lab)
    return [f"    {_A['d']}{lab[i]:>{w}}{_A['x']} {color}"
            f"{''.join(chr(_BR + c) for c in row)}{_A['x']}" for i, row in enumerate(grid)]


def emit(pods, swarm, t):
    live_eq = sum(x for x in (usd(p.state.get("equity"), p.state.get("quote_usd")) for p in pods) if x) or 0.0
    live_pnl = sum(x for x in (usd(p.state.get("pnl"), p.state.get("quote_usd")) for p in pods) if x) or 0.0
    live_fees = sum(x for x in (usd(p.state.get("fees_earned"), p.state.get("quote_usd")) for p in pods) if x) or 0.0
    # book total = live pods + everything already banked from retirements (honest cumulative)
    port_eq = live_eq + swarm.retired_pnl
    port_pnl = live_pnl + swarm.retired_pnl
    port_fees = live_fees + swarm.retired_fees
    # Chart the rotation-neutral curve: opening stake + cumulative pnl. Raw equity would jump
    # every time a fresh pod arms (new capital walks in) and misread as "growth" — pnl doesn't.
    if swarm.baseline is None and port_eq:
        swarm.baseline = port_eq
    base = swarm.baseline if swarm.baseline else (port_eq or 1.0)
    value = base + port_pnl if swarm.baseline else port_eq
    swarm.curve.append(value)
    pct = (port_pnl / base * 100.0) if base else 0.0
    up = port_pnl >= 0
    col = _A["g"] if up else _A["r"]
    arrow = "▲" if up else "▼"
    print(f"\n[fleet t+{t:>4}s] {_A['b']}PORTFOLIO ${value:,.2f}{_A['x']}  "
          f"{col}{arrow} {pct:+.2f}%{_A['x']}  {col}{port_pnl:+,.4f}${_A['x']}  fees {port_fees:.4f}$  "
          f"| {len(pods)} live · retired {swarm.retired} · perched {swarm.alarms} · "
          f"vetoed {swarm.vetoes} · blacklist {len(swarm.blacklist)} · tx-drops {LAT.drops}",
          flush=True)
    if len(swarm.curve) >= 2:
        for line in _chart(swarm.curve, col):
            print(line, flush=True)
    if GUI:
        GUI.update(swarm.curve, pct, up)
    for p in pods:
        s = p.state
        vpin = s.get("vpin")
        flow = (f" src={s.get('price_src','jup'):<3} vpin={vpin:.2f} imb={s.get('imbalance',0):+.2f} "
                f"{s.get('rate_eps',0):.1f}ev/s" if vpin is not None
                else f" src={s.get('price_src','jup'):<3}" if p.ws else "")
        print(f"    {p.symbol:18} {s.get('runtime_state',''):<8} {s.get('position',''):<8} "
              f"eq={s.get('equity',0):.4f} {p.quote:<5} pnl={s.get('pnl',0):+.4f} "
              f"fees={s.get('fees_earned',0):.5f} n={s.get('hawkes_n',0):.2f}{flow}", flush=True)


SOAK_CSV = os.environ.get("SOAK_CSV")
CSV_COLS = ["ts", "t", "symbol", "dexId", "liq", "turnover", "vol_h1", "vol_h6", "age_h",
            "state", "position", "quote", "quote_usd", "eq_q", "pnl_q", "fees_q",
            "usd_eq", "usd_pnl", "usd_fees", "hawkes_n", "tx_drops",
            "vpin", "imbalance", "flow_eps", "price_src"]


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
            vpin = s.get("vpin")
            w.writerow([f"{ts:.0f}", t, p.symbol, m.get("dexId", ""), f"{m.get('liq',0):.0f}",
                        f"{m.get('turnover',0):.3f}", f"{m.get('vol_h1',0):.2f}", f"{m.get('vol_h6',0):.2f}",
                        f"{m.get('age_h',0):.2f}", s.get("runtime_state", ""), s.get("position", ""),
                        p.quote, f"{q:.6f}" if q else "", f"{s.get('equity',0):.6f}",
                        f"{s.get('pnl',0):.6f}", f"{s.get('fees_earned',0):.6f}",
                        f"{usd(s.get('equity'),q) or 0:.4f}", f"{usd(s.get('pnl'),q) or 0:.4f}",
                        f"{usd(s.get('fees_earned'),q) or 0:.4f}", f"{s.get('hawkes_n',0):.3f}", LAT.drops,
                        f"{vpin:.3f}" if vpin is not None else "",
                        f"{s.get('imbalance',0):+.3f}", f"{s.get('rate_eps',0):.2f}",
                        s.get("price_src", "")])


def batch_prices(pods):
    """One Jupiter call for the whole book: {mint: usdPrice} for every base+quote mint."""
    mints = {m for p in pods for m in (p.base_mint, p.quote_mint) if m}
    return jupiter_usd_prices(sorted(mints))


async def rotate(pods, swarm, target, auto, now):
    """Retire dead/stale pods (bank their result), then refill to target from fresh
    selection. This is what makes the fleet a rolling book instead of a decaying snapshot."""
    # Mark staleness by FEE PROGRESS, not by whether a position is merely placed. A pod sitting
    # out-of-range (bid_out/ask_out) has a position but earns nothing — dead weight, exactly like
    # idle. Only actual fee income resets the clock, so a token that "collects nothing and takes up
    # space" ages out and frees its slot for a fresh candidate. Warm-up (HATCHING) is exempt.
    for p in pods:
        if p.state.get("runtime_state") == "HATCHING":
            p.stale = 0
            continue
        fees_now = float(p.state.get("fees_earned", 0.0))
        earning = fees_now > p.fee_mark + 1e-12   # did it collect any new fee this cycle?
        p.fee_mark = fees_now
        p.stale = 0 if earning else p.stale + 1
    # Remove ONLY the truly dead (blew the equity floor / refused at start) — always, and
    # remember them forever. A PERCHED pod is NOT dead: it's sitting in cash, defending.
    for p in list(pods):
        st = p.state.get("runtime_state")
        if st in ("VETOED", "ASHES"):
            swarm.retire(p, st, now, permanent=True)
            pods.remove(p)
    # Hit-and-run exits (max mode): dwell TTL + trailing stop. These fire UNCONDITIONALLY —
    # no replacement-first guard — because here the position is the risk and cash is the
    # defense. Waiting for a replacement before exiting would be the exact opposite of
    # "exit before it gets bad"; a temporarily short (even empty) fleet is the correct state.
    if MAX_DWELL or TRAIL_STOP_PCT or TOX_VPIN:
        t_now = time.time()
        for p in list(pods):
            pnl = float(p.state.get("pnl", 0.0))
            p.peak_pnl = max(p.peak_pnl, pnl)
            vpin = p.state.get("vpin")
            imb = p.state.get("imbalance", 0.0)
            if MAX_DWELL and (t_now - p.born) >= MAX_DWELL:
                swarm.retire(p, f"dwell {int(t_now - p.born)}s", now, permanent=False)
                pods.remove(p)
            elif TRAIL_STOP_PCT and (p.peak_pnl - pnl) >= AMOUNT * TRAIL_STOP_PCT / 100.0:
                swarm.retire(p, f"trail-stop peak{p.peak_pnl:+.4f}->{pnl:+.4f}", now, permanent=False)
                pods.remove(p)
            # Toxic-flow exit (all risk modes): one-sided SELL flow at high VPIN is the
            # earliest rug signature — it leads the price jump the Hawkes needs to see.
            # Get out while the exit is still orderly; cooldown, not blacklist.
            elif TOX_VPIN and vpin is not None and vpin >= TOX_VPIN and imb < -0.2:
                swarm.retire(p, f"toxic-flow vpin={vpin:.2f} imb={imb:+.2f}", now, permanent=False)
                pods.remove(p)
    if not auto or (now - swarm.last_refill) < REFILL_COOLDOWN:
        return
    stale = [p for p in pods if p.stale >= STALE_CYCLES]
    short = target - len(pods)  # empty slots left by dead pods
    if short <= 0 and not stale:
        return
    swarm.last_refill = now

    # Source replacements FIRST, then only rotate out a stale/perched pod if we actually got
    # something better to put in its place. In a dead market select returns nothing, so the
    # fleet HOLDS its defending pods and waits — instead of retiring them into an empty market
    # and starving to zero (the death spiral).
    held = {p.symbol for p in pods}
    want = short + len(stale)
    picks = await asyncio.to_thread(select_tokens, want + 3, swarm.excluded(now) | held)
    new_pods = []
    for m in picks:
        if len(new_pods) >= want:
            break
        if m["symbol"] in held:
            continue
        try:
            np = await asyncio.to_thread(Pod, m["addr"], m)
            new_pods.append(np)
            held.add(np.symbol)
            print(f"[fleet] add {np.symbol}", flush=True)
        except Exception as e:
            print(f"[fleet] skip {m['symbol']} ({e})", flush=True)
    # replacements backfill the dead slots first; any surplus SWAPS OUT stale pods (cooldown,
    # not permanent — a token that perched in a dump isn't bad forever)
    retire_n = max(0, len(new_pods) - short)
    for p in stale[:retire_n]:
        swarm.retire(p, f"stale {p.stale}c", now, permanent=False)
        pods.remove(p)
    pods.extend(new_pods)


async def main():
    global WSF
    if WS_FEED:
        try:
            from ws_feed import WsVaultFeed
            WSF = WsVaultFeed()
            WSF.start()
            print("[fleet] ws vault feed ON (racing free endpoints; WS_FEED=0 to disable)", flush=True)
        except Exception as e:
            print(f"[fleet] ws feed unavailable ({e}); Jupiter polling only", flush=True)
    explicit = [x for x in (os.environ.get("POOLS", "").split(",")) if x]
    persisted = load_blacklist()
    if persisted:
        print(f"[fleet] {len(persisted)} tokens on the persistent blacklist ({BLACKLIST_FILE})", flush=True)
    if explicit:
        picks = [{"addr": a} for a in explicit]
    else:
        picks = select_tokens(FLEET_SIZE, persisted)  # avoid known repeat offenders from the start
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
    swarm.blacklist = persisted  # carry cross-run memory into rotation
    if CHART_GUI:
        global GUI
        from chart_gui import GuiChart
        GUI = GuiChart()
    if ALERTS:
        global ALERTBOOK
        from alerts import AlertBook
        ALERTBOOK = AlertBook()
        print(f"[fleet] alerts ON ({'telegram' if ALERTBOOK.live else 'console only — set TELEGRAM_*'})",
              flush=True)
    auto = not explicit
    target = len(pods)  # hold the fleet at the size it successfully armed
    n = 0
    while True:
        if pods:
            # one batched Jupiter call for the whole book; per-pod fetch only for misses
            usdmap = await asyncio.to_thread(batch_prices, pods)
            fallbacks = []
            for p in pods:
                b, q = usdmap.get(p.base_mint), usdmap.get(p.quote_mint)
                if b and q and q > 0:
                    price = b / q
                    p.price, p.path.price, p.quote_usd = price, price, q
                else:
                    fallbacks.append(p)
            if fallbacks:
                infos = await asyncio.gather(*[asyncio.to_thread(p.feed.fetch) for p in fallbacks],
                                             return_exceptions=True)
                for p, info in zip(fallbacks, infos):
                    if not isinstance(info, Exception):
                        p.apply_price(info)
            # Live ws layer on top of the Jupiter base: fresher CPMM prices override, and
            # every pod's REAL trade flow feeds its VPIN meter + the controller's Hawkes
            # (sell-side arrivals only — it is a SELL-cascade detector, not an activity meter).
            if WSF is not None:
                wall_now = time.time()
                for p in pods:
                    if not p.ws:
                        continue
                    p.price_src = "jup"
                    lt = WSF.latest(p.symbol)
                    if lt and p.ws_price_ok and lt[0] and lt[2] <= WS_MAX_AGE_MS:
                        p.price = p.path.price = lt[0]
                        p.price_src = "ws"
                    evs = WSF.drain_events(p.symbol)
                    if evs:
                        p.flow.add(evs)
                        # map wall-clock arrivals into the controller's sim-clock domain:
                        # this tick spans (clock.now, clock.now+POLL]; place each event by
                        # its real recency inside that span.
                        base_clock = p.clock.now + POLL
                        sells = [base_clock - min(max(wall_now - ts / 1000.0, 0.0), POLL)
                                 for ts, side, _sz, _px in evs if side < 0]
                        if sells:
                            p.ctrl.feed_trade_events(sells)
            for p in pods:
                await p.tick(POLL)
                if WSF is not None and p.ws:
                    p.state.update(p.flow.metrics())
                    p.state["price_src"] = p.price_src
        swarm.update(pods)
        n += 1
        t = int(n * POLL)
        if ALERTBOOK is not None:
            # detection is pure+cheap; the Telegram POST is best-effort and kept off the loop
            for a in ALERTBOOK.scan(pods, t):
                await asyncio.to_thread(ALERTBOOK.send, a)
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
    except (KeyboardInterrupt, BrokenPipeError):
        pass
    finally:
        # A trailing `| tee` dies on the same Ctrl+C, so a final write can hit a dead pipe.
        # Data's already flushed to SOAK_CSV; just say goodbye without a traceback.
        try:
            print("\n[fleet] stopped")
        except BrokenPipeError:
            pass
