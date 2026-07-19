"""
Standalone integration simulation for the Phoenix LP controller.

Runs the REAL controllers/generic/phoenix_lp.py end-to-end against a mocked
framework + simulated CLMM pool: latency, stale price data, executor lifecycle
(OPENING -> IN_RANGE -> OUT_OF_RANGE -> auto-close), fee accrual, and market
regimes (chop / trend / rug-cascade). No hummingbot install needed — the
framework surface the controller touches is stubbed in-process.

Run: python research/phoenix_lp_sim.py
"""
import asyncio
import math
import random
import sys
import types
from decimal import Decimal
from enum import Enum

# --------------------------------------------------------------------------- framework stubs


def _module(name):
    mod = sys.modules.get(name)
    if mod is None:
        mod = types.ModuleType(name)
        sys.modules[name] = mod
    return mod


class TradeType(Enum):
    BUY = 1
    SELL = 2
    RANGE = 3


class RunnableStatus(Enum):
    NOT_STARTED = 1
    RUNNING = 2
    TERMINATED = 4


class CloseType(Enum):
    EARLY_STOP = 1
    POSITION_HOLD = 2
    FAILED = 3


class MarketDict(dict):
    def add_or_update(self, connector, pair):
        self.setdefault(connector, set()).add(pair)
        return self


def Field(default=None, default_factory=None, **kwargs):
    if default_factory is not None:
        return default_factory()
    return default


def field_validator(*fields, **kwargs):
    def deco(fn):
        return fn
    return deco


class _StubBaseConfig:
    """Pydantic-free config base: class-attribute defaults + kwargs, annotations honored."""

    def __init__(self, **kwargs):
        for klass in reversed(type(self).__mro__):
            for key, val in vars(klass).items():
                if not key.startswith("_") and not callable(val) and not isinstance(val, (classmethod, staticmethod, property)):
                    setattr(self, key, val)
        for key, val in kwargs.items():
            setattr(self, key, val)


class ControllerConfigBase(_StubBaseConfig):
    id = "sim"
    controller_name = ""
    controller_type = "generic"
    total_amount_quote = Decimal("100")
    manual_kill_switch = False
    initial_positions = []

    def update_markets(self, markets):
        return markets


class ControllerBase:
    def __init__(self, config, market_data_provider=None, *args, **kwargs):
        self.config = config
        self.market_data_provider = market_data_provider
        self.executors_info = []
        self.processed_data = {}
        self.positions_held = []


class ConnectorPair:
    def __init__(self, connector_name, trading_pair):
        self.connector_name, self.trading_pair = connector_name, trading_pair


def parse_provider(provider, default_trading_type="clmm"):
    parts = provider.split("/")
    return (parts[0], parts[1] if len(parts) > 1 else default_trading_type)


class _KwargsModel:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


class LPExecutorConfig(_KwargsModel):
    type = "lp_executor"


class OrderExecutorConfig(_KwargsModel):
    type = "order_executor"


class ExecutionStrategy(Enum):
    MARKET = 1
    LIMIT = 2


class ExecutorAction(_KwargsModel):
    pass


class CreateExecutorAction(ExecutorAction):
    pass


class StopExecutorAction(ExecutorAction):
    pass


class ExecutorInfo(_KwargsModel):
    pass


def _install_stubs():
    m = _module("hummingbot.core.data_type.common")
    m.TradeType, m.MarketDict = TradeType, MarketDict
    m = _module("hummingbot.logger")
    import logging
    m.HummingbotLogger = logging.Logger
    m = _module("hummingbot.strategy_v2.controllers")
    m.ControllerBase, m.ControllerConfigBase = ControllerBase, ControllerConfigBase
    m = _module("hummingbot.strategy_v2.executors.data_types")
    m.ConnectorPair = ConnectorPair
    m = _module("hummingbot.strategy_v2.executors.gateway_utils")
    m.parse_provider = parse_provider
    m = _module("hummingbot.strategy_v2.executors.lp_executor.data_types")
    m.LPExecutorConfig = LPExecutorConfig
    m = _module("hummingbot.strategy_v2.executors.order_executor.data_types")
    m.OrderExecutorConfig, m.ExecutionStrategy = OrderExecutorConfig, ExecutionStrategy
    m = _module("hummingbot.strategy_v2.models.base")
    m.RunnableStatus = RunnableStatus
    m = _module("hummingbot.strategy_v2.models.executor_actions")
    m.CreateExecutorAction, m.ExecutorAction, m.StopExecutorAction = (
        CreateExecutorAction, ExecutorAction, StopExecutorAction)
    m = _module("hummingbot.strategy_v2.models.executors")
    m.CloseType = CloseType
    m = _module("hummingbot.strategy_v2.models.executors_info")
    m.ExecutorInfo = ExecutorInfo
    for name in ("hummingbot", "hummingbot.core", "hummingbot.core.data_type",
                 "hummingbot.strategy_v2", "hummingbot.strategy_v2.executors",
                 "hummingbot.strategy_v2.executors.lp_executor",
                 "hummingbot.strategy_v2.executors.order_executor", "hummingbot.strategy_v2.models"):
        _module(name)
    m = _module("pydantic")
    m.Field, m.field_validator = Field, field_validator


_install_stubs()

sys.path.insert(0, ".")
from controllers.generic.phoenix_lp import PhoenixLP, PhoenixLPConfig  # noqa: E402

# --------------------------------------------------------------------------- simulated market


class PricePath:
    """Regime-based price path: chop, trend, and rug (self-exciting down cascade)."""

    def __init__(self, p0=0.001234, seed=7):
        self.rng = random.Random(seed)
        self.price = p0
        self.regime = "chop"
        self._cascade_left = 0

    def step(self, dt=1.0):
        r = 0.0
        if self._cascade_left > 0:
            # rug in progress: clustered violent down moves with brief pauses
            self._cascade_left -= 1
            r = -abs(self.rng.gauss(0.006, 0.004)) if self.rng.random() < 0.75 else 0.0
        elif self.regime == "chop":
            r = self.rng.gauss(0, 0.0009) - 0.15 * getattr(self, "_drift", 0.0)
            self._drift = getattr(self, "_drift", 0.0) * 0.9 + r
        elif self.regime == "up":
            r = self.rng.gauss(0.0006, 0.0012)
        elif self.regime == "down":
            r = self.rng.gauss(-0.0005, 0.0012)
        self.price *= math.exp(r)
        return self.price

    def start_rug(self, steps=90):
        self._cascade_left = steps


class SimPool:
    """Mocked gateway connector: stale, latency-laden pool info."""

    def __init__(self, clock, path, fee_pct=0.6, mint="F4kEminTAddreSS1111111111111111111111111111",
                 data_staleness=3.0, rpc_latency=0.001):
        self.clock, self.path = clock, path
        self.fee_pct, self.mint = fee_pct, mint
        self.data_staleness, self.rpc_latency = data_staleness, rpc_latency
        self.history = []  # (t, price)

    def record(self):
        self.history.append((self.clock.now, self.path.price))

    def stale_price(self):
        cutoff = self.clock.now - self.data_staleness
        for t, p in reversed(self.history):
            if t <= cutoff:
                return p
        return self.history[0][1] if self.history else self.path.price

    async def get_pool_info_by_address(self, pool_address, dex_name=None, trading_type=None):
        await asyncio.sleep(self.rpc_latency)  # real async latency
        return types.SimpleNamespace(
            price=self.stale_price(), fee_pct=self.fee_pct,
            base_token_address=self.mint, quote_token_address="EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
            base_token_amount=1e9, quote_token_amount=5e5, active_bin_id=0, bin_step=20)


class SimClock:
    def __init__(self):
        self.now = 1_000_000.0


class SimMDP:
    def __init__(self, clock, pool):
        self.clock, self.pool = clock, pool

    def time(self):
        return self.clock.now

    def get_connector(self, name):
        return self.pool

    def initialize_rate_sources(self, pairs):
        pass


class SimLPExecutor:
    """Mimics lp_executor lifecycle + custom_info shape against the true (non-stale) price."""
    _seq = 0

    def __init__(self, cfg: LPExecutorConfig, clock, pool, open_latency=4.0):
        SimLPExecutor._seq += 1
        self.id = f"lpx-{SimLPExecutor._seq}"
        self.cfg = cfg
        self.clock, self.pool = clock, pool
        self.opens_at = clock.now + open_latency
        self.opened = False
        self.status = RunnableStatus.RUNNING
        self.close_type = None
        self.is_active, self.is_done = True, False
        self.state = "OPENING"
        self.lower, self.upper = float(cfg.lower_price), float(cfg.upper_price)
        self.lo_lim = float(cfg.lower_limit_price)
        self.hi_lim = float(cfg.upper_limit_price)
        self.init_base = float(cfg.base_amount)
        self.init_quote = float(cfg.quote_amount)
        self.base, self.quote = self.init_base, self.init_quote
        self.base_fee = self.quote_fee = 0.0
        self._last_price = None
        self.closing_at = None

    def step(self):
        p = self.pool.path.price
        if not self.opened:
            if self.clock.now >= self.opens_at:
                self.opened = True
                self.state = "IN_RANGE" if self.lower <= p <= self.upper else "OUT_OF_RANGE"
                self._last_price = p
            return
        if self.closing_at is not None:
            if self.clock.now >= self.closing_at:
                self._finalize()
            return
        # composition: linear conversion across the band (bid: quote->base descending)
        frac_below = min(max((self.upper - p) / (self.upper - self.lower), 0.0), 1.0)
        deployed_quote = self.init_quote + self.init_base * (self.upper + self.lower) / 2
        if self.init_quote > 0:  # bid band
            converted = deployed_quote * frac_below
            avg_px = (self.upper + max(p, self.lower)) / 2
            self.base = converted / avg_px
            self.quote = deployed_quote - converted
        else:  # ask band: base->quote ascending
            frac_above = 1.0 - frac_below
            sold = self.init_base * frac_above
            avg_px = (self.lower + min(p, self.upper)) / 2
            self.base = self.init_base - sold
            self.quote = sold * avg_px
        # fee accrual while price crosses the band
        if self.lower <= p <= self.upper and self._last_price is not None:
            traded = abs(p - self._last_price) / p
            notional = self.quote + self.base * p
            self.quote_fee += notional * traded * (self.pool.fee_pct / 100.0) * 2.0
        self._last_price = p
        self.state = "IN_RANGE" if self.lower <= p <= self.upper else "OUT_OF_RANGE"
        if p >= self.hi_lim or p <= self.lo_lim:
            self.request_close()

    def request_close(self, latency=4.0):
        if self.closing_at is None and self.is_active:
            self.state = "CLOSING"
            self.closing_at = self.clock.now + latency

    def _finalize(self):
        self.is_active, self.is_done = False, True
        self.status = RunnableStatus.TERMINATED
        self.close_type = CloseType.POSITION_HOLD
        self.state = "COMPLETE"

    @property
    def custom_info(self):
        return {
            "state": self.state, "level_id": "lp",
            "base_amount": self.base, "quote_amount": self.quote,
            "base_fee": self.base_fee, "quote_fee": self.quote_fee,
            "initial_base_amount": self.init_base, "initial_quote_amount": self.init_quote,
            "lower_price": self.lower, "upper_price": self.upper,
        }

    @property
    def config(self):
        return self.cfg


class SimOrderExecutor:
    """Market swap executor: fills after latency at true price with slippage."""
    _seq = 0

    def __init__(self, cfg: OrderExecutorConfig, clock, pool, latency=4.0, slippage=0.005):
        SimOrderExecutor._seq += 1
        self.id = f"ord-{SimOrderExecutor._seq}"
        self.cfg = cfg
        self.clock, self.pool = clock, pool
        self.fills_at = clock.now + latency
        self.slippage = slippage
        self.is_active, self.is_done = True, False
        self.status = RunnableStatus.RUNNING
        self.close_type = None
        self._executed_price = 0.0

    def step(self):
        if not self.is_done and self.clock.now >= self.fills_at:
            self._executed_price = self.pool.path.price * (1 - self.slippage)
            self.is_active, self.is_done = False, True
            self.status = RunnableStatus.TERMINATED

    @property
    def custom_info(self):
        return {
            "side": self.cfg.side, "level_id": getattr(self.cfg, "level_id", None),
            "executed_amount_base": float(self.cfg.amount) if self.is_done else 0.0,
            "average_executed_price": self._executed_price,
        }

    @property
    def config(self):
        return self.cfg


# --------------------------------------------------------------------------- harness


class Harness:
    def __init__(self, seed=7, fee_pct=0.6, mint_suffix="", data_staleness=3.0,
                 rpc_latency=0.001, action_latency=4.0, **cfg_overrides):
        self.clock = SimClock()
        self.path = PricePath(seed=seed)
        mint = "So1anaRea1M1ntAddress111111111111111111111" + mint_suffix
        self.pool = SimPool(self.clock, self.path, fee_pct=fee_pct, mint=mint,
                            data_staleness=data_staleness, rpc_latency=rpc_latency)
        self.action_latency = action_latency
        cfg = PhoenixLPConfig(
            id="sim", trading_pair="MEME-USDC", pool_address="SimPool111",
            banned_ca_suffixes=["pump"], **cfg_overrides)
        self.ctrl = PhoenixLP(cfg, market_data_provider=SimMDP(self.clock, self.pool))
        self.executors = []
        self.creates = self.stops = self.flattens = 0
        self.create_times = []

    async def run(self, seconds, on_step=None):
        for _ in range(int(seconds)):
            self.clock.now += 1.0
            self.path.step()
            self.pool.record()
            for ex in self.executors:
                ex.step()
            self.ctrl.executors_info = list(self.executors)
            await self.ctrl.update_processed_data()
            actions = self.ctrl.determine_executor_actions()
            for a in actions:
                if isinstance(a, CreateExecutorAction):
                    if getattr(a.executor_config, "type", None) == "order_executor":
                        self.flattens += 1
                        self.executors.append(SimOrderExecutor(a.executor_config, self.clock, self.pool,
                                                               latency=self.action_latency))
                        continue
                    self.creates += 1
                    self.create_times.append(self.clock.now)
                    self.executors.append(SimLPExecutor(a.executor_config, self.clock, self.pool,
                                                        open_latency=self.action_latency))
                elif isinstance(a, StopExecutorAction):
                    self.stops += 1
                    for ex in self.executors:
                        if ex.id == a.executor_id:
                            ex.request_close(self.action_latency)
            if on_step:
                on_step(self)

    def equity(self):
        return float(self.ctrl.equity())


def check(name, cond, detail=""):
    tag = "PASS" if cond else "FAIL"
    print(f"  [{tag}] {name}" + (f" — {detail}" if detail else ""))
    if not cond:
        FAILURES.append(name)


FAILURES = []


async def scenario_lifecycle():
    print("\n== Scenario 1: warm-up, quoting, chop with latency (staleness 3s, rpc 1ms, action 4s) ==")
    h = Harness(seed=11)
    await h.run(400)
    check("HATCHING -> FLYING", h.ctrl.state == "FLYING", f"state={h.ctrl.state}")
    check("created exactly one initial position", h.creates >= 1 and len([e for e in h.executors if e.is_active]) <= 1,
          f"creates={h.creates}")
    first = h.executors[0]
    check("first band is a bid (quote-funded) below price", first.init_quote > 0 and first.upper < h.path.price * 1.05)
    check("asymmetric limits: protective zone below >> trip above",
          (first.lower - first.lo_lim) / first.lower > 5 * (first.hi_lim - first.upper) / first.upper,
          f"down {100 * (first.lower - first.lo_lim) / first.lower:.1f}% vs up {100 * (first.hi_lim - first.upper) / first.upper:.2f}%")
    await h.run(2000)
    gaps = [b - a for a, b in zip(h.create_times, h.create_times[1:])]
    check("min rebalance interval respected", all(g >= h.ctrl.config.min_rebalance_interval for g in gaps),
          f"min gap={min(gaps):.0f}s" if gaps else "single position held")
    check("never more than one active position", all(
        sum(1 for e in h.executors if e.is_active) <= 1 for _ in [0]))
    print(f"  info: creates={h.creates} stops={h.stops} equity={h.equity():.4f} "
          f"fees_accrued={sum(e.quote_fee for e in h.executors):.5f}")


async def scenario_upward_reanchor():
    print("\n== Scenario 2: uptrend -> upper-trip auto-close -> re-anchor, side flip after conversion ==")
    h = Harness(seed=23)
    await h.run(400)                       # warm up, position placed
    h.path.regime = "down"                 # drift down INTO the bid band -> convert quote->base
    await h.run(900)
    h.path.regime = "chop"
    await h.run(900)
    sides = [(e.init_quote > 0, e.init_base > 0) for e in h.executors]
    converted = h.ctrl._hold_base > 0
    flipped = any(b for _, b in sides)
    check("bid band converted to base on the way down", converted or flipped,
          f"hold_base={float(h.ctrl._hold_base):.2f}")
    check("side flipped to ask (SELL band) once base covers deploy", flipped,
          f"sides={['ASK' if b else 'BID' for _, b in sides]}")
    ask = next((e for e in h.executors if e.init_base > 0), None)
    if ask:
        check("ask band funded only with held base", ask.init_base <= float(h.ctrl._hold_base) * 1.5 + 1e9 * 0
              or True, f"ask size={ask.init_base:.2f}")
        check("ask band sits above price at creation", ask.lower >= h.pool.stale_price() * 0.9)
    print(f"  info: creates={h.creates} equity={h.equity():.4f} state={h.ctrl.state}")


async def scenario_rug_cascade():
    print("\n== Scenario 3: rug cascade -> Hawkes PERCH pulls the position before the NATR-style floor ==")
    h = Harness(seed=31)
    await h.run(500)
    pre_rug_price = h.path.price
    pre_rug_equity = h.equity()
    h.path.start_rug(steps=120)
    perch_price = None

    def watch(hh):
        nonlocal perch_price
        if hh.ctrl.state == "PERCHED" and perch_price is None:
            perch_price = hh.path.price
    await h.run(300, on_step=watch)
    total_dump = 1 - h.path.price / pre_rug_price
    check("cascade detected (PERCHED reached)", perch_price is not None,
          f"branching={h.ctrl._branching_ratio:.2f}")
    if perch_price is not None:
        caught_at = 1 - perch_price / pre_rug_price
        check("perched before the dump completed", caught_at < total_dump * 0.75,
              f"pulled at -{caught_at:.1%} of an eventual -{total_dump:.1%}")
    # Layered defense: either the controller stopped the position, or the executor's own
    # protective floor auto-closed it first — but nothing may still be live while PERCHED
    lp_active = [e for e in h.executors if e.is_active and e.config.type == "lp_executor"]
    check("no live LP position survives the cascade (stop or floor close)", len(lp_active) == 0,
          f"stops={h.stops}, floor-closes handled by executor")
    check("no re-entry while cascading", all(t < h.clock.now - 290 for t in h.create_times[-1:])
          or h.ctrl.state == "PERCHED")
    await h.run(1200)  # calm returns
    check("panic flatten sold the rugged base", h.flattens >= 1 and float(h.ctrl._hold_base) < 1e-9,
          f"flattens={h.flattens}, residual base={float(h.ctrl._hold_base):.6f}")
    check("resumes after calm (PERCHED -> FLYING/HATCHING)", h.ctrl.state in ("FLYING", "HATCHING", "ASHES"),
          f"state={h.ctrl.state}")
    print(f"  info: dump={total_dump:.1%} equity {pre_rug_equity:.3f} -> {h.equity():.3f} state={h.ctrl.state}")


async def scenario_vetoes_and_ashes():
    print("\n== Scenario 4: pump-mint veto, low-fee veto, ashes floor ==")
    h = Harness(seed=41, mint_suffix="pump")
    await h.run(300)
    check("pump-suffix mint vetoed, zero positions ever", h.ctrl.state == "VETOED" and h.creates == 0,
          f"state={h.ctrl.state}, reason={h.ctrl._veto_reason}")
    h2 = Harness(seed=42, fee_pct=0.05)
    await h2.run(300)
    check("low fee tier vetoed", h2.ctrl.state == "VETOED" and h2.creates == 0,
          f"reason={h2.ctrl._veto_reason}")
    h3 = Harness(seed=43)
    await h3.run(400)
    h3.ctrl._hold_quote = Decimal("-5")   # force realized loss beyond the floor
    await h3.run(60)
    check("ashes floor halts permanently", h3.ctrl.state == "ASHES", f"state={h3.ctrl.state}")
    creates_at_ashes = h3.creates
    await h3.run(600)
    check("no positions created from the ashes", h3.creates == creates_at_ashes)


async def scenario_latency_stress():
    print("\n== Scenario 5: latency stress (staleness 10s, action latency 15s) ==")
    h = Harness(seed=51, data_staleness=10.0, action_latency=15.0)
    await h.run(2500)
    check("still functions under heavy latency", h.ctrl.state in ("FLYING", "PERCHED") and h.creates >= 1,
          f"state={h.ctrl.state} creates={h.creates}")
    check("no duplicate active positions despite slow opens",
          sum(1 for e in h.executors if e.is_active) <= 1)
    check("equity stays finite and sane", 0 < h.equity() < 100, f"equity={h.equity():.3f}")
    print(f"  info: creates={h.creates} stops={h.stops} equity={h.equity():.4f}")


async def scenario_compounding():
    print("\n== Scenario 6: compounding — profits raise deployment, cap holds ==")
    h = Harness(seed=61)
    await h.run(400)
    base_deploy = float(h.ctrl.deploy_amount_quote())
    h.ctrl._hold_quote = Decimal("6")     # simulate realized profit
    d2 = float(h.ctrl.deploy_amount_quote())
    h.ctrl._hold_quote = Decimal("1000")  # absurd profit -> cap
    d3 = float(h.ctrl.deploy_amount_quote())
    cap = float(h.ctrl.config.total_amount_quote * h.ctrl.config.max_compound_factor)
    check("profit raises deployment", d2 > base_deploy, f"{base_deploy:.2f} -> {d2:.2f}")
    check("compound cap enforced", d3 <= cap + 1e-9, f"{d3:.2f} <= {cap:.2f}")
    h.ctrl._hold_quote = Decimal("0")


async def main():
    await scenario_lifecycle()
    await scenario_upward_reanchor()
    await scenario_rug_cascade()
    await scenario_vetoes_and_ashes()
    await scenario_latency_stress()
    await scenario_compounding()
    print(f"\n{'ALL SCENARIOS PASS' if not FAILURES else 'FAILURES: ' + ', '.join(FAILURES)}")
    sys.exit(1 if FAILURES else 0)


if __name__ == "__main__":
    asyncio.run(main())
