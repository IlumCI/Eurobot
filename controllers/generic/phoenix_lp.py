import logging
import math
from collections import deque
from decimal import Decimal
from typing import Deque, List, Optional, Tuple

from pydantic import Field, field_validator

from hummingbot.core.data_type.common import MarketDict, TradeType
from hummingbot.logger import HummingbotLogger
from hummingbot.strategy_v2.controllers import ControllerBase, ControllerConfigBase
from hummingbot.strategy_v2.executors.data_types import ConnectorPair
from hummingbot.strategy_v2.executors.gateway_utils import parse_provider
from hummingbot.strategy_v2.executors.lp_executor.data_types import LPExecutorConfig
from hummingbot.strategy_v2.executors.order_executor.data_types import ExecutionStrategy, OrderExecutorConfig
from hummingbot.strategy_v2.models.base import RunnableStatus
from hummingbot.strategy_v2.models.executor_actions import CreateExecutorAction, ExecutorAction, StopExecutorAction
from hummingbot.strategy_v2.models.executors import CloseType
from hummingbot.strategy_v2.models.executors_info import ExecutorInfo


class PhoenixLPConfig(ControllerConfigBase):
    """
    Phoenix LP: micro-bankroll CLMM liquidity provision for Solana pools.
    Deploy: start --script v2_with_controllers.py --conf v2_phoenix_lp.yml
    (preset: conf/controllers/phoenix_lp.yml)
    """
    controller_type: str = "generic"
    controller_name: str = "phoenix_lp"

    # Venue
    connector_name: str = "solana-mainnet-beta"
    lp_provider: str = "meteora/clmm"
    trading_pair: str
    pool_address: str

    # Venue-selection guards (the research says the edge IS the venue)
    banned_ca_suffixes: List[str] = Field(
        default=["pump"],
        description="Refuse to run if the base token mint ends with any of these (e.g. pump.fun launches)")
    min_pool_fee_pct: Decimal = Field(
        default=Decimal("0.25"),
        description="Refuse pools below this fee tier (%); low-fee pools lose the fees-vs-LVR race")

    # Bankroll & sizing (fractional-Kelly compounding with a hard floor)
    total_amount_quote: Decimal = Field(default=Decimal("10"), json_schema_extra={"is_updatable": True})
    kelly_fraction: Decimal = Field(
        default=Decimal("0.4"), gt=0, le=1,
        json_schema_extra={"is_updatable": True},
        description="Fraction of live equity deployed into the band (sub-Kelly; the rest stays in reserve)")
    max_compound_factor: Decimal = Field(default=Decimal("5"), json_schema_extra={"is_updatable": True})
    ashes_floor_pct: Decimal = Field(
        default=Decimal("0.6"),
        description="Halt permanently if equity falls below this fraction of the starting bankroll")

    # Avellaneda-Stoikov band geometry (reservation price -> band center, spread -> width)
    as_gamma: Decimal = Field(default=Decimal("0.8"), json_schema_extra={"is_updatable": True},
                              description="Risk aversion: scales inventory skew and vol term of the width")
    as_kappa: Decimal = Field(default=Decimal("150"), json_schema_extra={"is_updatable": True},
                              description="Fill-intensity proxy: higher = tighter base spread "
                                          "(base half-spread ~= ln(1+gamma/kappa)/gamma ~= 1/kappa)")
    as_horizon_seconds: int = Field(default=3600, description="A-S horizon T for the vol terms")
    min_width_pct: Decimal = Field(default=Decimal("0.4"), json_schema_extra={"is_updatable": True})
    max_width_pct: Decimal = Field(default=Decimal("8"), json_schema_extra={"is_updatable": True})
    trend_lean: Decimal = Field(
        default=Decimal("0.5"), ge=0, le=1,
        json_schema_extra={"is_updatable": True},
        description="How strongly the EMA+RSI trend score shifts the band center (fraction of width)")

    # Asymmetric tau-reset (protective downside, fast upside re-anchor)
    upper_rebalance_pct: Decimal = Field(
        default=Decimal("0.5"), json_schema_extra={"is_updatable": True},
        description="% beyond the band's upper bound that triggers close-and-replace (chase upward exits fast)")
    protective_zone_pct: Decimal = Field(
        default=Decimal("8"), json_schema_extra={"is_updatable": True},
        description="% below the band's lower bound before a forced close (dumps inside this zone do NOT rebalance)")
    min_rebalance_interval: int = Field(
        default=300, json_schema_extra={"is_updatable": True},
        description="Seconds between position closes and re-opens; over-rebalancing is the documented LP killer")
    flip_hysteresis: Decimal = Field(
        default=Decimal("0.1"),
        description="Base-value fraction beyond 0.5 needed to flip quoting side (avoids side thrash)")

    # Price sampling & signals
    sample_interval: int = Field(default=5, description="Seconds between stored price samples")
    vol_window: int = Field(default=240, description="Samples kept for volatility/trend estimation")
    ema_fast: int = Field(default=20)
    ema_slow: int = Field(default=80)
    rsi_length: int = Field(default=14)
    min_samples: int = Field(default=60, description="Samples required before quoting")

    # Hawkes cascade detector (self-excitation of adverse price moves)
    hawkes_event_pct: Decimal = Field(
        default=Decimal("0.3"),
        description="Down-move (%) between samples that counts as one sell-pressure event")
    hawkes_window: int = Field(default=900, description="Seconds of events kept for the Hawkes fit")
    hawkes_decay_seconds: Decimal = Field(default=Decimal("30"), description="Exponential kernel decay timescale")
    hawkes_min_events: int = Field(default=5)
    panic_branching_ratio: Decimal = Field(
        default=Decimal("0.7"), json_schema_extra={"is_updatable": True},
        description="Branching ratio above which quotes are pulled (sell flow feeding on itself)")
    resume_branching_ratio: Decimal = Field(default=Decimal("0.4"))
    panic_flatten: bool = Field(
        default=True,
        description="On PERCHED/ASHES, market-sell held base via the swap provider: exit the token, not just the position")

    # Connector-specific (e.g. Meteora strategyType)
    strategy_type: Optional[int] = Field(default=None)

    @field_validator("banned_ca_suffixes", mode="before")
    @classmethod
    def parse_suffixes(cls, v):
        if isinstance(v, str):
            return [s.strip().lower() for s in v.split(",") if s.strip()]
        return [s.lower() for s in (v or [])]

    def update_markets(self, markets: MarketDict) -> MarketDict:
        return markets.add_or_update(self.connector_name, self.trading_pair)


class PhoenixLP(ControllerBase):
    """
    Everything the esoteric-strategy research survived adversarial verification with,
    integrated on the LP executor (see research/esoteric_strategies.md):

    1. Venue guards — pump.fun mint veto (CA suffix) and a minimum pool fee tier, because
       long-tail high-fee pools are the only regime where fees beat adverse selection.
    2. Asymmetric tau-reset bands — a protective zone below the band so dumps do not
       crystallize IL by triggering relocation, while upward exits re-anchor fast; plus a
       hard minimum rebalance interval (over-rebalancing is the documented -100% CAR killer).
    3. Avellaneda-Stoikov geometry — inventory-skewed reservation price sets the band
       center, the A-S optimal spread sets the band width, adapted to CLMM bounds.
    4. Hawkes cascade detector — an exponential-kernel Hawkes branching ratio fitted by EM
       on down-move events from the pool price stream; when sell pressure becomes
       self-exciting the position is pulled BEFORE a volatility measure would react.
    5. Fractional-Kelly compounding — deploys a sub-Kelly fraction of live equity
       (bankroll + realized PnL from position hold), capped, with a permanent ashes floor.

    States: HATCHING (gathering data) -> FLYING (quoting) -> PERCHED (cascade panic)
            -> ASHES (equity floor breached, permanent) / VETOED (venue guard, permanent)
    """

    _logger: Optional[HummingbotLogger] = None

    @classmethod
    def logger(cls) -> HummingbotLogger:
        if cls._logger is None:
            cls._logger = logging.getLogger(__name__)
        return cls._logger

    def __init__(self, config: PhoenixLPConfig, *args, **kwargs):
        super().__init__(config, *args, **kwargs)
        self.config: PhoenixLPConfig = config
        self.lp_dex_name, self.lp_trading_type = parse_provider(config.lp_provider, default_trading_type="clmm")
        parts = config.trading_pair.split("-")
        self._base_token = parts[0] if len(parts) >= 2 else ""
        self._quote_token = parts[1] if len(parts) >= 2 else ""

        self.state: str = "HATCHING"
        self._veto_reason: Optional[str] = None
        self._pool_price: Optional[Decimal] = None
        self._pool_fee_pct: Optional[Decimal] = None
        self._venue_checked: bool = False

        # Price samples (timestamp, price) and down-move event times for the Hawkes fit
        self._samples: Deque[Tuple[float, float]] = deque(maxlen=config.vol_window)
        self._events: Deque[float] = deque(maxlen=500)
        self._branching_ratio: float = 0.0
        self._sigma_sample: float = 0.0  # stdev of log returns per sample
        self._trend: float = 0.0

        # Executor tracking + realized position hold (net token change across closed positions)
        self._current_executor_id: Optional[str] = None
        self._stop_requested_id: Optional[str] = None
        self._flatten_executor_id: Optional[str] = None
        self._last_close_ts: float = 0.0
        self._sigma_robust: float = 0.0  # MAD-based sigma; cascades can't inflate it
        self._hold_base: Decimal = Decimal("0")
        self._hold_quote: Decimal = Decimal("0")
        self._quoting_side: TradeType = TradeType.BUY  # start bid-side: bankroll begins in quote

        self.market_data_provider.initialize_rate_sources([
            ConnectorPair(connector_name=config.connector_name, trading_pair=config.trading_pair)])

    # ------------------------------------------------------------------ data & signals

    async def update_processed_data(self):
        try:
            connector = self.market_data_provider.get_connector(self.config.connector_name)
            pool_info = await connector.get_pool_info_by_address(
                self.config.pool_address, dex_name=self.lp_dex_name, trading_type=self.lp_trading_type)
        except Exception as e:
            self.logger().debug(f"Pool info fetch failed: {e}")
            return
        if not pool_info or not pool_info.price:
            return

        self._pool_price = Decimal(str(pool_info.price))
        self._pool_fee_pct = Decimal(str(getattr(pool_info, "fee_pct", 0)))
        if not self._venue_checked:
            self._check_venue(pool_info)

        now = self.market_data_provider.time()
        if not self._samples or now - self._samples[-1][0] >= self.config.sample_interval:
            prev = self._samples[-1][1] if self._samples else None
            self._samples.append((now, float(pool_info.price)))
            if prev and prev > 0:
                move = float(pool_info.price) / prev - 1.0
                # Adaptive event threshold: 3x robust (MAD) sigma so ordinary chop never
                # emits events, while a cascade cannot inflate its own detection bar
                threshold = max(float(self.config.hawkes_event_pct) / 100.0, 3.0 * self._sigma_robust)
                if move <= -threshold:
                    # Marked events: a k-threshold move counts k times (capped). Without this a
                    # violent cascade saturates to one event per sample — a REGULAR stream the
                    # EM rightly reads as background rate, not self-excitation.
                    for k in range(min(int(-move / threshold), 10)):
                        self._events.append(now + 0.05 * k)
            self._recompute_signals(now)

        self.processed_data = {
            "state": self.state, "price": self._pool_price, "trend": self._trend,
            "sigma": self._sigma_sample, "branching_ratio": self._branching_ratio,
            "equity": self.equity(),
        }

    def _check_venue(self, pool_info):
        """Permanent veto on pump-suffixed base mints and fee tiers too low to beat LVR."""
        mint = (getattr(pool_info, "base_token_address", "") or "")
        for suffix in self.config.banned_ca_suffixes:
            if mint.lower().endswith(suffix):
                self.state, self._veto_reason = "VETOED", f"base mint ends with '{suffix}': {mint}"
                self.logger().warning(f"Phoenix VETO: {self._veto_reason}")
                self._venue_checked = True
                return
        if self._pool_fee_pct is not None and self._pool_fee_pct < self.config.min_pool_fee_pct:
            self.state = "VETOED"
            self._veto_reason = f"pool fee {self._pool_fee_pct}% < min {self.config.min_pool_fee_pct}%"
            self.logger().warning(f"Phoenix VETO: {self._veto_reason}")
        self._venue_checked = True

    def _recompute_signals(self, now: float):
        prices = [p for _, p in self._samples]
        if len(prices) >= 3:
            rets = [math.log(prices[i] / prices[i - 1]) for i in range(1, len(prices)) if prices[i - 1] > 0]
            mean = sum(rets) / len(rets)
            self._sigma_sample = math.sqrt(sum((r - mean) ** 2 for r in rets) / max(len(rets) - 1, 1))
            srt = sorted(rets)
            med = srt[len(srt) // 2]
            mad = sorted(abs(r - med) for r in rets)[len(rets) // 2]
            self._sigma_robust = 1.4826 * mad
        if len(prices) >= self.config.min_samples:
            self._trend = self._trend_score(prices)
        cutoff = now - self.config.hawkes_window
        while self._events and self._events[0] < cutoff:
            self._events.popleft()
        # Composite cascade score. The EM branching ratio reads clustering PATTERN — it
        # catches slow-building cascades and stays elevated after a storm (blocking
        # re-entry) — but a live constant-intensity storm is correctly explained by
        # background rate, so it goes blind mid-rug. The intensity term covers that:
        # under the 3-sigma adaptive threshold the chop baseline is ~zero events, so a
        # burst of marked events inside two decay windows IS the cascade, live.
        em_n = self._hawkes_branching_ratio(list(self._events), now)
        recent_window = 2.0 * float(self.config.hawkes_decay_seconds)
        recent = sum(1 for t in self._events if t > now - recent_window)
        intensity = min(recent / (2.0 * self.config.hawkes_min_events), 0.95)
        self._branching_ratio = max(em_n, intensity)

    def _trend_score(self, prices: List[float]) -> float:
        """EMA distance (vol-normalized) + RSI bias, clipped to [-1, 1]."""
        def ema(series, span):
            k, e = 2.0 / (span + 1.0), series[0]
            for p in series[1:]:
                e = p * k + e * (1.0 - k)
            return e
        fast, slow = ema(prices, self.config.ema_fast), ema(prices, self.config.ema_slow)
        ema_dist = (fast - slow) / prices[-1]
        scale = max(self._sigma_sample * math.sqrt(self.config.ema_slow), 1e-9)
        ema_score = max(-1.0, min(1.0, ema_dist / scale))
        n = self.config.rsi_length
        gains = losses = 0.0
        for i in range(max(1, len(prices) - n), len(prices)):
            d = prices[i] - prices[i - 1]
            gains, losses = gains + max(d, 0.0), losses + max(-d, 0.0)
        rsi_score = 0.0 if gains + losses == 0 else (gains - losses) / (gains + losses)
        return max(-1.0, min(1.0, 0.6 * ema_score + 0.4 * rsi_score))

    def _hawkes_branching_ratio(self, events: List[float], now: float) -> float:
        """
        EM fit of an exponential-kernel Hawkes process (fixed decay beta) on down-move
        event times; returns the branching ratio n = alpha/beta in [0, 1). n -> 1 means
        each sell event spawns ~1 more: a self-feeding cascade (rug/liquidation spiral).
        """
        if len(events) < self.config.hawkes_min_events:
            return 0.0
        beta = 1.0 / float(self.config.hawkes_decay_seconds)
        window = max(now - events[0], 1.0)
        mu, n = len(events) / window * 0.5, 0.5
        for _ in range(15):
            exo = endo = 0.0
            for i, ti in enumerate(events):
                excitation = sum(n * beta * math.exp(-beta * (ti - tj))
                                 for tj in events[max(0, i - 30):i] if ti > tj)
                lam = mu + excitation
                if lam <= 0:
                    continue
                exo += mu / lam
                endo += excitation / lam
            mu = max(exo / window, 1e-9)
            n = min(endo / len(events), 0.98)
        return n

    # ------------------------------------------------------------------ sizing & accounting

    def equity(self) -> Decimal:
        """Realized equity: bankroll + net token change from all closed positions, marked at pool price."""
        price = self._pool_price or Decimal("0")
        return self.config.total_amount_quote + self._hold_quote + self._hold_base * price

    def deploy_amount_quote(self) -> Decimal:
        eq = max(self.equity(), Decimal("0"))
        cap = self.config.total_amount_quote * self.config.max_compound_factor
        return min(eq * self.config.kelly_fraction, cap)

    def _active_executor(self) -> Optional[ExecutorInfo]:
        active = [e for e in self.executors_info
                  if e.is_active and getattr(e.config, "type", None) == "lp_executor"]
        return active[0] if active else None

    def _settle_closed_executor(self):
        """Fold a terminated executor's net token change (incl. fees) into the position hold."""
        if not self._current_executor_id:
            return
        executor = next((e for e in self.executors_info if e.id == self._current_executor_id), None)
        if executor is None or executor.status != RunnableStatus.TERMINATED:
            return
        if executor.close_type != CloseType.FAILED:
            c = executor.custom_info
            base_net = (Decimal(str(c.get("base_amount", 0))) + Decimal(str(c.get("base_fee", 0)))
                        - Decimal(str(c.get("initial_base_amount", 0))))
            quote_net = (Decimal(str(c.get("quote_amount", 0))) + Decimal(str(c.get("quote_fee", 0)))
                         - Decimal(str(c.get("initial_quote_amount", 0))))
            self._hold_base += base_net
            self._hold_quote += quote_net
            self.logger().info(
                f"Position settled: net {base_net:+.6f} {self._base_token}, {quote_net:+.6f} {self._quote_token} | "
                f"hold: {self._hold_base:.6f}/{self._hold_quote:.6f} | equity {self.equity():.4f}")
        self._current_executor_id = None
        self._last_close_ts = self.market_data_provider.time()

    def available_quote(self) -> Decimal:
        """Quote actually spendable: bankroll plus net quote change from closed positions."""
        return max(self.config.total_amount_quote + self._hold_quote, Decimal("0"))

    def _settle_flatten_executor(self):
        """Fold a completed panic-flatten swap into the position hold."""
        if not self._flatten_executor_id:
            return
        if self._flatten_executor_id == "pending":
            candidates = [e for e in self.executors_info
                          if getattr(e.config, "type", None) == "order_executor"
                          and getattr(e.config, "level_id", None) == "panic_flatten"]
            if not candidates:
                return
            self._flatten_executor_id = candidates[-1].id
        ex = next((e for e in self.executors_info if e.id == self._flatten_executor_id), None)
        if ex is None or not ex.is_done:
            return
        if ex.close_type != CloseType.FAILED:
            executed = Decimal(str(ex.custom_info.get("executed_amount_base", 0)))
            px = Decimal(str(ex.custom_info.get("average_executed_price", 0)))
            self._hold_base -= executed
            self._hold_quote += executed * px
            self.logger().info(
                f"Panic flatten filled: sold {executed:.6f} {self._base_token} @ {px:.8f} | equity {self.equity():.4f}")
        self._flatten_executor_id = None

    def _panic_flatten_action(self) -> Optional[CreateExecutorAction]:
        """Market-sell held base while panicked; only after the LP position is closed and settled."""
        if not self.config.panic_flatten or self._flatten_executor_id is not None:
            return None
        if self._current_executor_id is not None or self._active_executor() is not None:
            return None  # position tokens are not in the wallet until the LP close settles
        price = self._pool_price or Decimal("0")
        if price <= 0 or self._hold_base * price < Decimal("1"):
            return None  # nothing worth a swap fee
        order = OrderExecutorConfig(
            timestamp=self.market_data_provider.time(),
            connector_name=self.config.connector_name,
            trading_pair=self.config.trading_pair,
            side=TradeType.SELL,
            amount=self._hold_base,
            execution_strategy=ExecutionStrategy.MARKET,
            price=price,
            level_id="panic_flatten",
        )
        self._flatten_executor_id = "pending"
        self.logger().warning(
            f"Panic flatten: selling {self._hold_base:.6f} {self._base_token} to exit the token entirely.")
        return CreateExecutorAction(controller_id=self.config.id, executor_config=order)

    def _pick_side(self) -> TradeType:
        """
        Quote the side our inventory can fund: the ask (SELL band) once held base covers
        at least half the next deployment, back to the bid (BUY band) once it cannot.
        Hysteresis prevents side thrash at the boundary.
        """
        price = self._pool_price or Decimal("0")
        deploy = self.deploy_amount_quote()
        if deploy <= 0 or price <= 0:
            return self._quoting_side
        base_value = self._hold_base * price
        coverage = base_value / deploy
        if self._quoting_side == TradeType.BUY and coverage > Decimal("0.5") + self.config.flip_hysteresis:
            self._quoting_side = TradeType.SELL
        elif self._quoting_side == TradeType.SELL and coverage < Decimal("0.5") - self.config.flip_hysteresis:
            self._quoting_side = TradeType.BUY
        return self._quoting_side

    # ------------------------------------------------------------------ band geometry (A-S)

    def _band(self, side: TradeType, price: Decimal) -> Tuple[Decimal, Decimal, Decimal, Decimal]:
        """
        Avellaneda-Stoikov adapted to a CLMM band:
        reservation shift = -q * gamma * sigma^2 * T  (inventory pushes quotes away from risk)
        half-spread      = gamma * sigma^2 * T / 2 + (1/gamma) * ln(1 + gamma/kappa)
        Then the asymmetric tau-reset limits: tight above (re-anchor fast on upward exit),
        protective zone below (dumps do not trigger relocation until the hard floor).
        Returns (lower_price, upper_price, lower_limit, upper_limit).
        """
        gamma, kappa = float(self.config.as_gamma), float(self.config.as_kappa)
        sigma2_t = (self._sigma_sample ** 2) * (self.config.as_horizon_seconds / max(self.config.sample_interval, 1))

        base_value = float(self._hold_base * price)
        total = base_value + float(self._hold_quote + self.config.total_amount_quote)
        q = 0.0 if total <= 0 else max(-1.0, min(1.0, (2.0 * base_value / total) - 1.0))

        half_spread = gamma * sigma2_t / 2.0 + math.log(1.0 + gamma / kappa) / gamma
        width = max(float(self.config.min_width_pct) / 100.0,
                    min(2.0 * half_spread, float(self.config.max_width_pct) / 100.0))
        shift = -q * gamma * sigma2_t + float(self.config.trend_lean) * self._trend * width
        center = float(price) * (1.0 + shift)

        if side == TradeType.BUY:  # bid band: quote-only, entirely below price
            upper = min(center, float(price) * 0.9995)
            lower = upper * (1.0 - width)
        else:  # ask band: base-only, entirely above price
            lower = max(center, float(price) * 1.0005)
            upper = lower * (1.0 + width)

        upper_limit = upper * (1.0 + float(self.config.upper_rebalance_pct) / 100.0)
        lower_limit = lower * (1.0 - float(self.config.protective_zone_pct) / 100.0)
        return (Decimal(str(lower)), Decimal(str(upper)), Decimal(str(lower_limit)), Decimal(str(upper_limit)))

    def _create_executor_config(self) -> Optional[LPExecutorConfig]:
        price = self._pool_price
        if price is None or price <= 0:
            return None
        deploy = self.deploy_amount_quote()
        if deploy <= 0:
            return None
        side = self._pick_side()
        lower, upper, lower_limit, upper_limit = self._band(side, price)
        if lower >= upper:
            return None
        # Clamp to inventory the wallet can actually fund; the chain rejects unfunded adds
        if side == TradeType.BUY:
            base_amt, quote_amt = Decimal("0"), min(deploy, self.available_quote())
            funded = quote_amt
        else:
            base_amt, quote_amt = min(deploy / price, max(self._hold_base, Decimal("0"))), Decimal("0")
            funded = base_amt * price
        if funded < Decimal("1"):  # below any CLMM min-deposit worth paying rent for
            return None
        extra = {"strategyType": self.config.strategy_type} if self.config.strategy_type is not None else None
        self.logger().info(
            f"Phoenix band: {side.name} [{lower:.8f}, {upper:.8f}] limits [{lower_limit:.8f}, {upper_limit:.8f}] "
            f"deploy {deploy:.4f} {self._quote_token} | trend {self._trend:+.2f} "
            f"sigma/sample {self._sigma_sample:.5f} n_hawkes {self._branching_ratio:.2f}")
        return LPExecutorConfig(
            timestamp=self.market_data_provider.time(),
            connector_name=self.config.connector_name,
            lp_provider=self.config.lp_provider,
            trading_pair=self.config.trading_pair,
            pool_address=self.config.pool_address,
            lower_price=lower, upper_price=upper,
            base_amount=base_amt, quote_amount=quote_amt,
            side=side,
            upper_limit_price=upper_limit, lower_limit_price=lower_limit,
            extra_params=extra,
            keep_position=True,
        )

    # ------------------------------------------------------------------ control loop

    def determine_executor_actions(self) -> List[ExecutorAction]:
        actions: List[ExecutorAction] = []
        now = self.market_data_provider.time()
        active = self._active_executor()
        if active and not self._current_executor_id:
            self._current_executor_id = active.id
        if not active:
            self._settle_closed_executor()
        self._settle_flatten_executor()

        # State transitions
        if self.state not in ("VETOED", "ASHES"):
            if self._pool_price and self.equity() <= self.config.total_amount_quote * self.config.ashes_floor_pct:
                self.state = "ASHES"
                self.logger().warning(f"Phoenix ASHES: equity {self.equity():.4f} breached the floor. Halting.")
            elif self._branching_ratio >= float(self.config.panic_branching_ratio):
                if self.state != "PERCHED":
                    self.logger().warning(
                        f"Phoenix PERCHED: Hawkes branching ratio {self._branching_ratio:.2f} — cascade detected.")
                self.state = "PERCHED"
            elif self.state == "PERCHED" and self._branching_ratio <= float(self.config.resume_branching_ratio):
                self.state = "HATCHING" if len(self._samples) < self.config.min_samples else "FLYING"
            elif self.state in ("HATCHING", "FLYING"):
                self.state = "HATCHING" if len(self._samples) < self.config.min_samples else "FLYING"

        # Halt states and panic: pull any live position, dump held base, create nothing
        if self.state in ("VETOED", "ASHES", "PERCHED"):
            if active and active.id != self._stop_requested_id:
                self._stop_requested_id = active.id
                actions.append(StopExecutorAction(controller_id=self.config.id, executor_id=active.id))
            if self.state in ("ASHES", "PERCHED"):
                flatten = self._panic_flatten_action()
                if flatten:
                    actions.append(flatten)
            return actions

        # FLYING: keep exactly one position, respecting the minimum rebalance interval
        if self.state == "FLYING" and active is None and self._flatten_executor_id is None:
            if now - self._last_close_ts >= self.config.min_rebalance_interval:
                executor_config = self._create_executor_config()
                if executor_config:
                    actions.append(CreateExecutorAction(controller_id=self.config.id,
                                                        executor_config=executor_config))
        return actions

    # ------------------------------------------------------------------ status

    def to_format_status(self) -> List[str]:
        price = self._pool_price or Decimal("0")
        eq = self.equity()
        pnl = eq - self.config.total_amount_quote
        lines = [
            f"Phoenix LP [{self.state}] {self.config.trading_pair} @ {self.config.lp_provider} "
            f"(pool fee: {self._pool_fee_pct or '?'}%)",
            f"  equity: {eq:.4f} {self._quote_token} (pnl {pnl:+.4f}) | deploy: {self.deploy_amount_quote():.4f} "
            f"| hold: {self._hold_base:.6f} {self._base_token} / {self._hold_quote:.4f} {self._quote_token}",
            f"  price: {price:.8f} | trend: {self._trend:+.2f} | sigma/sample: {self._sigma_sample:.5f} "
            f"| hawkes n: {self._branching_ratio:.2f} (panic at {self.config.panic_branching_ratio})",
        ]
        if self._veto_reason:
            lines.append(f"  VETO: {self._veto_reason}")
        if self._sigma_sample > 0 and self.config.sample_interval > 0:
            samples_per_day = 86400.0 / self.config.sample_interval
            sigma_daily = self._sigma_sample * math.sqrt(samples_per_day)
            lvr_daily_pct = (sigma_daily ** 2) / 8.0 * 100.0
            lines.append(
                f"  LVR gauge: sigma_daily ~{sigma_daily:.2%} -> adverse selection ~{lvr_daily_pct:.3f}%/day "
                f"vs pool fee {self._pool_fee_pct or '?'}% per fill")
        active = self._active_executor()
        if active:
            c = active.custom_info
            lines.append(
                f"  position [{c.get('state', '?')}]: [{c.get('lower_price', 0)}, {c.get('upper_price', 0)}] "
                f"fees {c.get('base_fee', 0)}/{c.get('quote_fee', 0)}")
        return lines
