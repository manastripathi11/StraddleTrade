"""Shared test fixtures and factory helpers for NautilusTrader models."""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

from nautilus_trader.cache.cache import Cache
from nautilus_trader.common.component import LiveClock, MessageBus
from nautilus_trader.core.uuid import UUID4
from nautilus_trader.model.currencies import BTC, USDT
from nautilus_trader.model.enums import (
    LiquiditySide,
    OptionKind,
    OrderSide,
    OrderType,
    PositionSide,
)
from nautilus_trader.model.events import (
    OrderAccepted,
    OrderCanceled,
    OrderFilled,
    OrderRejected,
    OrderSubmitted,
    PositionClosed,
    PositionOpened,
)
from nautilus_trader.model.identifiers import (
    AccountId,
    ClientOrderId,
    InstrumentId,
    PositionId,
    StrategyId,
    Symbol,
    TradeId,
    TraderId,
    VenueOrderId,
)
from nautilus_trader.model.instruments import CryptoOption
from nautilus_trader.model.objects import Money, Price, Quantity
from nautilus_trader.portfolio.portfolio import Portfolio
from nautilus_trader.trading.strategy import Strategy

DEFAULT_TRADER_ID = TraderId("TRADER-01")
DEFAULT_STRATEGY_ID = StrategyId("STRAT-01")
DEFAULT_ACCOUNT_ID = AccountId("BYBIT-ACC-01")
DEFAULT_INSTRUMENT_ID = InstrumentId.from_str("BTC-TEST-OPTION.BYBIT")


def register_test_strategy(strategy: Strategy, cache: Cache | None = None) -> Cache:
    """Wire official NautilusTrader dependencies (Cache, Portfolio, MessageBus, Clock) into Strategy."""
    actual_cache = cache if cache is not None else Cache()
    clock = LiveClock()
    msgbus = MessageBus(trader_id=DEFAULT_TRADER_ID, clock=clock)
    portfolio = Portfolio(msgbus=msgbus, cache=actual_cache, clock=clock)
    strategy.register(DEFAULT_TRADER_ID, portfolio, msgbus, actual_cache, clock)
    return actual_cache


def make_mock_crypto_option(
    raw_symbol: str,
    option_kind: OptionKind,
    strike_price: float,
    expiration_utc: datetime | None = None,
) -> CryptoOption:
    """Build a valid CryptoOption model with exact Cython positional parameters."""
    if expiration_utc is None:
        expiration_utc = (datetime.now(timezone.utc) + timedelta(days=7)).replace(microsecond=0)

    activation_ns = int((expiration_utc - timedelta(days=30)).timestamp() * 1e9)
    expiration_ns = int(expiration_utc.timestamp() * 1e9)

    return CryptoOption(
        InstrumentId.from_str(f"{raw_symbol}-OPTION.BYBIT"),
        Symbol(raw_symbol),
        BTC,
        USDT,
        USDT,
        False,  # is_inverse
        option_kind,
        Price.from_str(f"{strike_price:.0f}"),
        activation_ns,
        expiration_ns,
        0,  # price_precision
        2,  # size_precision
        Price.from_str("5"),  # price_increment
        Quantity.from_str("0.01"),  # size_increment
        0,  # ts_event
        0,  # ts_init
    )


def make_order_submitted_event(client_order_id: str) -> OrderSubmitted:
    now_ns = time.time_ns()
    return OrderSubmitted(
        DEFAULT_TRADER_ID,
        DEFAULT_STRATEGY_ID,
        DEFAULT_INSTRUMENT_ID,
        ClientOrderId(client_order_id),
        DEFAULT_ACCOUNT_ID,
        UUID4(),
        now_ns,
        now_ns,
    )


def make_order_accepted_event(client_order_id: str, venue_order_id: str = "VENUE-001") -> OrderAccepted:
    now_ns = time.time_ns()
    return OrderAccepted(
        DEFAULT_TRADER_ID,
        DEFAULT_STRATEGY_ID,
        DEFAULT_INSTRUMENT_ID,
        ClientOrderId(client_order_id),
        VenueOrderId(venue_order_id),
        DEFAULT_ACCOUNT_ID,
        UUID4(),
        now_ns,
        now_ns,
    )


def make_order_rejected_event(client_order_id: str, reason: str = "Order rejected") -> OrderRejected:
    now_ns = time.time_ns()
    return OrderRejected(
        DEFAULT_TRADER_ID,
        DEFAULT_STRATEGY_ID,
        DEFAULT_INSTRUMENT_ID,
        ClientOrderId(client_order_id),
        DEFAULT_ACCOUNT_ID,
        reason,
        UUID4(),
        now_ns,
        now_ns,
    )


def make_order_canceled_event(client_order_id: str) -> OrderCanceled:
    now_ns = time.time_ns()
    return OrderCanceled(
        DEFAULT_TRADER_ID,
        DEFAULT_STRATEGY_ID,
        DEFAULT_INSTRUMENT_ID,
        ClientOrderId(client_order_id),
        VenueOrderId("VENUE-001"),
        DEFAULT_ACCOUNT_ID,
        UUID4(),
        now_ns,
        now_ns,
    )


def make_order_filled_event(
    client_order_id: str,
    fill_qty: str = "0.01",
    fill_px: str = "1000",
) -> OrderFilled:
    now_ns = time.time_ns()
    return OrderFilled(
        DEFAULT_TRADER_ID,
        DEFAULT_STRATEGY_ID,
        DEFAULT_INSTRUMENT_ID,
        ClientOrderId(client_order_id),
        VenueOrderId("VENUE-001"),
        DEFAULT_ACCOUNT_ID,
        TradeId("TRADE-001"),
        PositionId("POS-001"),
        OrderSide.SELL,
        OrderType.MARKET,
        Quantity.from_str(fill_qty),
        Price.from_str(fill_px),
        USDT,
        Money.from_str("0.0 USDT"),
        LiquiditySide.TAKER,
        UUID4(),
        now_ns,
        now_ns,
    )


def make_position_opened_event(instrument_id: InstrumentId) -> PositionOpened:
    now_ns = time.time_ns()
    return PositionOpened(
        DEFAULT_TRADER_ID,
        DEFAULT_STRATEGY_ID,
        instrument_id,
        PositionId("POS-001"),
        DEFAULT_ACCOUNT_ID,
        ClientOrderId("ORD-001"),
        OrderSide.SELL,
        PositionSide.SHORT,
        -0.01,
        Quantity.from_str("0.01"),
        Quantity.from_str("0.01"),
        Quantity.from_str("0.01"),
        Price.from_str("1500"),
        USDT,
        1500.0,
        Money.from_str("0.0 USDT"),
        UUID4(),
        now_ns,
        now_ns,
    )


def make_position_closed_event(instrument_id: InstrumentId) -> PositionClosed:
    now_ns = time.time_ns()
    return PositionClosed(
        DEFAULT_TRADER_ID,
        DEFAULT_STRATEGY_ID,
        instrument_id,
        PositionId("POS-001"),
        DEFAULT_ACCOUNT_ID,
        ClientOrderId("ORD-001"),
        ClientOrderId("ORD-002"),
        OrderSide.SELL,
        PositionSide.FLAT,
        0.0,
        Quantity.from_str("0.01"),
        Quantity.from_str("0.01"),
        Quantity.from_str("0.01"),
        Price.from_str("1400"),
        USDT,
        1500.0,
        1400.0,
        0.066,
        Money.from_str("1.0 USDT"),
        UUID4(),
        now_ns - 1000,
        now_ns,
        1000,
        now_ns,
    )
