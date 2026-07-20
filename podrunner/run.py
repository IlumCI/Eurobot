#!/usr/bin/env python3
"""Valtgeist pod runtime — PAPER mode against a LIVE pool.

Runs the REAL Phoenix LP controller (controllers/generic/phoenix_lp.py) against a
real Solana pool's live price (via DexScreener), but with paper money: no funds
move, the LP position and equity are simulated. Every tick it streams telemetry
to the control plane so the user's dashboard comes alive with genuine market
behaviour — warm-up, quoting, band placement, a cascade pull-out if the market
rugs — all real, all risk-free.

This is the demo / paper build. The real build keeps this exact controller and
swaps the paper executors for Gateway CLMM orders behind the user's vault; the
telemetry path and control plane are identical, which is the whole point.

Config comes from the environment (the orchestrator injects it per pod):
  POD_ID, POD_TOKEN          — identity + per-pod telemetry credential
  TELEMETRY_URL, TELEMETRY_APIKEY — control-plane ingest endpoint + gateway key
  POOL_ADDRESS               — Solana pool to track (default: BONK-USDC on Raydium)
  POOL_FEE_PCT, AMOUNT_QUOTE, RISK, POLL_SECONDS
  DRY_RUN=1                  — print telemetry to stdout instead of POSTing
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

import phoenix_lp_sim as sim  # noqa: E402  installs the framework stubs + imports the real controller
from live_feed import LiveFeed  # noqa: E402

POD_ID = os.environ.get("POD_ID")
POD_TOKEN = os.environ.get("POD_TOKEN")
TELEMETRY_URL = os.environ.get("TELEMETRY_URL")
TELEMETRY_APIKEY = os.environ.get("TELEMETRY_APIKEY")
POOL = os.environ.get("POOL_ADDRESS", "4RX3HeVhvDT1N2Qnn9wMVtHfSGE3NqU3GYnuhaCoKDUD")  # BONK-USDC, Raydium
FEE_PCT = float(os.environ.get("POOL_FEE_PCT", "0.25"))
AMOUNT = float(os.environ.get("AMOUNT_QUOTE", "10"))
RISK = os.environ.get("RISK", "balanced")
POLL = float(os.environ.get("POLL_SECONDS", "6"))
DRY = os.environ.get("DRY_RUN") == "1"

# Risk appetite -> the two knobs the dashboard exposes; everything else stays on
# the researched defaults, matching dashboard/src/PodConfig.jsx.
RISK_MAP = {
    "conservative": {"kelly_fraction": "0.25", "ashes_floor_pct": "0.7"},
    "balanced": {"kelly_fraction": "0.4", "ashes_floor_pct": "0.6"},
    "aggressive": {"kelly_fraction": "0.6", "ashes_floor_pct": "0.5"},
}


class LivePath:
    """Price source with the same surface the paper machinery expects, but its
    value is the real pool price — set by the runtime each poll, not random-walked."""

    def __init__(self, price):
        self.price = price

    def step(self, dt=1.0):  # no-op: the runtime injects the real price between ticks
        return self.price

    def start_rug(self, steps=90):
        pass


def build_controller(price, mint, symbol):
    risk = RISK_MAP.get(RISK, RISK_MAP["balanced"])
    cfg = sim.PhoenixLPConfig(
        id=POD_ID or "demo",
        trading_pair=symbol,
        pool_address=POOL,
        banned_ca_suffixes=["pump"],
        total_amount_quote=Decimal(str(AMOUNT)),
        kelly_fraction=Decimal(risk["kelly_fraction"]),
        ashes_floor_pct=Decimal(risk["ashes_floor_pct"]),
        # Demo warm-up: one price sample per poll, quoting after ~12 samples (~72s at 6s
        # polls) instead of the production 5 minutes. Live-money pods use the defaults.
        sample_interval=int(POLL),
        min_samples=12,
        vol_window=60,
        ema_fast=6,
        ema_slow=12,
        hawkes_min_events=3,
    )
    clock = sim.SimClock()
    path = LivePath(price)
    pool = sim.SimPool(clock, path, fee_pct=FEE_PCT, mint=mint)
    ctrl = sim.PhoenixLP(cfg, market_data_provider=sim.SimMDP(clock, pool))
    return ctrl, clock, pool, path


async def tick(ctrl, clock, pool, executors, dt):
    """One logical step: advance the clock, price the paper positions against the
    live price, let the controller decide, and apply its actions. Mirrors the
    proven Harness loop in phoenix_lp_sim.py."""
    clock.now += dt
    pool.record()
    for ex in executors:
        ex.step()
    ctrl.executors_info = list(executors)
    await ctrl.update_processed_data()
    for a in ctrl.determine_executor_actions():
        if isinstance(a, sim.CreateExecutorAction):
            if getattr(a.executor_config, "type", None) == "order_executor":
                executors.append(sim.SimOrderExecutor(a.executor_config, clock, pool, latency=4.0))
            else:
                executors.append(sim.SimLPExecutor(a.executor_config, clock, pool, open_latency=4.0))
        elif isinstance(a, sim.StopExecutorAction):
            for ex in executors:
                if ex.id == a.executor_id and hasattr(ex, "request_close"):
                    ex.request_close(4.0)


def extract(ctrl, executors, price):
    """Pull the dashboard telemetry out of the live controller state."""
    active = None
    for ex in executors:
        info = getattr(ex, "custom_info", {}) or {}
        if getattr(ex, "is_active", False) and "lower_price" in info:
            active = info
    state = {
        "runtime_state": ctrl.state,
        "equity": float(ctrl.equity()),
        "deploy": float(ctrl.deploy_amount_quote()),
        "trend": float(ctrl._trend),
        "hawkes_n": float(ctrl._branching_ratio),
    }
    if active:
        state["band_lower"] = float(active["lower_price"])
        state["band_upper"] = float(active["upper_price"])
    sig = ctrl._sigma_sample
    if sig > 0 and ctrl.config.sample_interval > 0:
        samples_per_day = 86400.0 / float(ctrl.config.sample_interval)
        sigma_daily = sig * math.sqrt(samples_per_day)
        state["lvr_daily"] = (sigma_daily ** 2) / 8.0 * 100.0
    return state


def post_telemetry(state):
    body = json.dumps({"pod_id": POD_ID, "token": POD_TOKEN, "state": state}).encode()
    req = urllib.request.Request(
        TELEMETRY_URL,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "apikey": TELEMETRY_APIKEY,
            "Authorization": f"Bearer {TELEMETRY_APIKEY}",
        },
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        return r.status, r.read().decode()


async def main():
    if not DRY and not all([POD_ID, POD_TOKEN, TELEMETRY_URL, TELEMETRY_APIKEY]):
        sys.exit("Set POD_ID, POD_TOKEN, TELEMETRY_URL, TELEMETRY_APIKEY (or DRY_RUN=1).")

    feed = LiveFeed(POOL)
    info = feed.fetch()
    print(f"[podrunner] pod={POD_ID or 'demo'} risk={RISK} | live pool {info['symbol']} on {info['dex']} "
          f"@ {info['price']:.10g} (mint {info['base_mint'][:6]}… liq ${(info['liquidity_usd'] or 0)/1e6:.1f}M)",
          flush=True)

    ctrl, clock, pool, path = build_controller(info["price"], info["base_mint"], info["symbol"])
    executors = []
    n = 0
    while True:
        try:
            info = feed.fetch()
            path.price = info["price"]
        except Exception as e:
            print(f"[podrunner] feed error ({e}); holding last price", flush=True)
        await tick(ctrl, clock, pool, executors, POLL)
        state = extract(ctrl, executors, path.price)
        n += 1
        if DRY:
            band = (f"[{state['band_lower']:.8g},{state['band_upper']:.8g}]"
                    if "band_lower" in state else "—")
            print(f"t+{int(n * POLL):>4}s {state['runtime_state']:<8} eq={state['equity']:.4f} "
                  f"px={path.price:.8g} trend={state['trend']:+.2f} n={state['hawkes_n']:.2f} band={band}",
                  flush=True)
        else:
            try:
                code, resp = post_telemetry(state)
                if code != 200:
                    print(f"[podrunner] telemetry {code}: {resp}", flush=True)
            except Exception as e:
                print(f"[podrunner] telemetry POST failed: {e}", flush=True)
        await asyncio.sleep(POLL)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[podrunner] stopped")
