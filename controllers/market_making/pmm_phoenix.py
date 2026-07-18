import math
from decimal import Decimal
from typing import List, Optional, Tuple

import pandas_ta as ta  # noqa: F401
from pydantic import Field, field_validator
from pydantic_core.core_schema import ValidationInfo

from hummingbot.core.data_type.common import PriceType, TradeType
from hummingbot.data_feed.candles_feed.data_types import CandlesConfig
from hummingbot.strategy_v2.controllers.market_making_controller_base import (
    MarketMakingControllerBase,
    MarketMakingControllerConfigBase,
)
from hummingbot.strategy_v2.executors.position_executor.data_types import PositionExecutorConfig
from hummingbot.strategy_v2.models.executor_actions import ExecutorAction, StopExecutorAction


class PMMPhoenixConfig(MarketMakingControllerConfigBase):
    """
    Phoenix: a micro-bankroll market maker. Deploy with:
      start --script v2_with_controllers.py --conf v2_phoenix.yml
    (see conf/controllers/phoenix_micro.yml for the $10 preset)
    """
    controller_name: str = "pmm_phoenix"
    candles_connector: str = Field(
        default=None,
        json_schema_extra={
            "prompt": "Candles connector, empty to reuse the trading connector: ", "prompt_on_new": True})
    candles_trading_pair: str = Field(
        default=None,
        json_schema_extra={
            "prompt": "Candles pair, empty to reuse the trading pair: ", "prompt_on_new": True})
    interval: str = Field(
        default="1m",
        json_schema_extra={"prompt": "Candle interval (e.g., 1m, 3m): ", "prompt_on_new": True})
    natr_length: int = Field(default=30, json_schema_extra={"is_updatable": True})
    ema_fast: int = Field(default=8, json_schema_extra={"is_updatable": True})
    ema_slow: int = Field(default=34, json_schema_extra={"is_updatable": True})
    rsi_length: int = Field(default=14, json_schema_extra={"is_updatable": True})
    maker_fee_pct: Decimal = Field(
        default=Decimal("0.0002"),
        json_schema_extra={"prompt": "Maker fee as a decimal (0.0002 = 2 bps): ", "prompt_on_new": True})
    min_profit_pct: Decimal = Field(default=Decimal("0.0004"), json_schema_extra={"is_updatable": True})
    trend_lean: Decimal = Field(default=Decimal("0.5"), json_schema_extra={"is_updatable": True})
    trend_spread_skew: Decimal = Field(default=Decimal("0.3"), ge=0, le=1, json_schema_extra={"is_updatable": True})
    trend_size_skew: Decimal = Field(default=Decimal("0.4"), ge=0, le=1, json_schema_extra={"is_updatable": True})
    panic_natr_mult: Decimal = Field(default=Decimal("3"), json_schema_extra={"is_updatable": True})
    ashes_floor_pct: Decimal = Field(default=Decimal("0.6"), json_schema_extra={"is_updatable": True})
    max_compound_factor: Decimal = Field(default=Decimal("5"), json_schema_extra={"is_updatable": True})

    @field_validator("candles_connector", "candles_trading_pair", mode="before")
    @classmethod
    def set_candles_defaults(cls, v, validation_info: ValidationInfo):
        if v is None or v == "":
            key = "connector_name" if validation_info.field_name == "candles_connector" else "trading_pair"
            return validation_info.data.get(key)
        return v

    @property
    def fee_floor(self) -> Decimal:
        """Minimum half-spread that still profits after both maker fills."""
        return Decimal("2") * self.maker_fee_pct + self.min_profit_pct


class PMMPhoenixController(MarketMakingControllerBase):
    """
    Market maker built for a bankroll so small that every basis point matters.

    Four edges over a plain PMM, each addressing a way $10 usually dies:
    1. Fee-floor spreads — quotes never tighter than round-trip fees + margin,
       and take-profit scales with live volatility (fees are the #1 killer).
    2. Momentum lean — an EMA+RSI score in [-1, 1] shifts the reference price,
       tightens the with-trend quote and up-sizes it, so trends fill the right
       side instead of running over inventory (adverse selection is #2).
    3. Volatility circuit breaker — when NATR spikes past its recent median,
       quotes are pulled until the storm passes (one wick can erase the stack).
    4. Compounding with an ashes floor — orders are sized off live equity
       (bankroll + realized PnL) so wins grow the quotes, while quoting halts
       for good if equity falls below the floor (the phoenix keeps its ashes).
    """

    def __init__(self, config: PMMPhoenixConfig, *args, **kwargs):
        self.config = config
        self.max_records = max(config.natr_length, config.ema_slow, config.rsi_length) + 60
        super().__init__(config, *args, **kwargs)

    def get_candles_config(self) -> List[CandlesConfig]:
        return [CandlesConfig(connector=self.config.candles_connector, trading_pair=self.config.candles_trading_pair,
                              interval=self.config.interval, max_records=self.max_records)]

    @property
    def realized_pnl(self) -> Decimal:
        return sum((e.net_pnl_quote for e in self.executors_info if e.is_done), Decimal("0"))

    @property
    def equity(self) -> Decimal:
        return self.config.total_amount_quote + self.realized_pnl

    async def update_processed_data(self):
        mid = Decimal(self.market_data_provider.get_price_by_type(
            self.config.connector_name, self.config.trading_pair, PriceType.MidPrice))
        state = {"reference_price": mid, "spread_multiplier": Decimal("1"), "state": "HATCHING",
                 "trend": Decimal("0"), "natr": None, "equity": self.equity}

        candles = self.market_data_provider.get_candles_df(
            connector_name=self.config.candles_connector, trading_pair=self.config.candles_trading_pair,
            interval=self.config.interval, max_records=self.max_records)
        if len(candles) < self.max_records - 10:
            self.processed_data = state  # not enough history yet: stay grounded, quote nothing
            return

        close = candles["close"]
        natr = ta.natr(candles["high"], candles["low"], close, length=self.config.natr_length) / 100
        ema_dist = (ta.ema(close, length=self.config.ema_fast) - ta.ema(close, length=self.config.ema_slow)) / close
        rsi = ta.rsi(close, length=self.config.rsi_length)
        latest = [natr.iloc[-1], natr.median(), ema_dist.iloc[-1], rsi.iloc[-1]]
        if any(math.isnan(float(v)) for v in latest):
            self.processed_data = state  # degenerate candle data (flat/gappy): quote nothing
            return
        natr_now, natr_median = Decimal(str(natr.iloc[-1])), Decimal(str(natr.median()))
        # Trend score: EMA distance normalized by volatility (60%) + RSI bias (40%), clipped to [-1, 1]
        ema_score = max(-1.0, min(1.0, float(ema_dist.iloc[-1]) / max(float(natr.iloc[-1]), 1e-9)))
        rsi_score = (float(rsi.iloc[-1]) - 50.0) / 50.0
        trend = Decimal(str(max(-1.0, min(1.0, 0.6 * ema_score + 0.4 * rsi_score))))

        if self.equity <= self.config.total_amount_quote * self.config.ashes_floor_pct:
            state["state"] = "ASHES"  # drawdown floor breached: stop quoting, keep what remains
        elif natr_now > natr_median * self.config.panic_natr_mult:
            state["state"] = "PERCHED"  # volatility spike: pull quotes and wait it out
        else:
            state["state"] = "FLYING"

        state.update({
            "reference_price": mid * (1 + self.config.trend_lean * natr_now * trend),
            "spread_multiplier": natr_now,
            "buy_spread_multiplier": natr_now * (1 - self.config.trend_spread_skew * trend),
            "sell_spread_multiplier": natr_now * (1 + self.config.trend_spread_skew * trend),
            "take_profit": max(natr_now, self.config.fee_floor),
            "trend": trend, "natr": natr_now, "natr_median": natr_median,
        })
        self.processed_data = state

    def get_price_and_amount(self, level_id: str) -> Tuple[Decimal, Decimal]:
        level = self.get_level_from_level_id(level_id)
        trade_type = self.get_trade_type_from_level_id(level_id)
        spreads, amounts_quote = self.config.get_spreads_and_amounts_in_quote(trade_type)
        side = "buy" if trade_type == TradeType.BUY else "sell"
        multiplier = Decimal(self.processed_data.get(f"{side}_spread_multiplier",
                                                     self.processed_data["spread_multiplier"]))
        spread_pct = max(Decimal(str(spreads[level])) * multiplier, self.config.fee_floor)
        side_sign = Decimal("-1") if trade_type == TradeType.BUY else Decimal("1")
        price = Decimal(self.processed_data["reference_price"]) * (1 + side_sign * spread_pct)

        # Size off live equity (compounding) and lean into the trend
        trend = self.processed_data["trend"]
        compound = min(max(self.equity, Decimal("0")) / self.config.total_amount_quote,
                       self.config.max_compound_factor)
        size_lean = 1 - side_sign * self.config.trend_size_skew * trend  # buys grow when bullish, sells when bearish
        amount_quote = Decimal(str(amounts_quote[level])) * compound * size_lean

        rules = self.market_data_provider.get_trading_rules(self.config.connector_name, self.config.trading_pair)
        # A $10 book cannot afford a rejected order: bump sub-minimum quotes up to the exchange floor
        amount_quote = max(amount_quote, rules.min_notional_size * Decimal("1.02"))
        price = self.market_data_provider.quantize_order_price(
            self.config.connector_name, self.config.trading_pair, price)
        amount = self.market_data_provider.quantize_order_amount(
            self.config.connector_name, self.config.trading_pair, amount_quote / price)
        return price, amount

    def get_executor_config(self, level_id: str, price: Decimal, amount: Decimal) -> Optional[PositionExecutorConfig]:
        if self.processed_data.get("state") != "FLYING" or amount <= 0:
            return None
        barriers = self.config.triple_barrier_config.new_instance_with_adjusted_volatility(
            float(self.processed_data["take_profit"] / self.config.take_profit)
        ) if self.config.take_profit else self.config.triple_barrier_config
        return PositionExecutorConfig(
            timestamp=self.market_data_provider.time(), level_id=level_id,
            connector_name=self.config.connector_name, trading_pair=self.config.trading_pair,
            entry_price=price, amount=amount, triple_barrier_config=barriers,
            leverage=self.config.leverage, side=self.get_trade_type_from_level_id(level_id),
        )

    def executors_to_early_stop(self) -> List[ExecutorAction]:
        """Pull resting (not yet filled) quotes the moment the bot stops FLYING."""
        if self.processed_data.get("state") == "FLYING":
            return []
        stale = self.filter_executors(executors=self.executors_info,
                                      filter_func=lambda x: x.is_active and not x.is_trading)
        return [StopExecutorAction(controller_id=self.config.id, executor_id=e.id) for e in stale]

    def to_format_status(self) -> List[str]:
        d = self.processed_data
        natr = f"{d['natr']:.4%}" if d.get("natr") is not None else "n/a"
        return [f"Phoenix [{d.get('state', '?')}] | equity: {d.get('equity', 0):.4f} "
                f"(realized: {self.realized_pnl:+.4f}) | trend: {d.get('trend', 0):+.2f} | "
                f"NATR: {natr} | fee floor: {self.config.fee_floor:.4%}"]
