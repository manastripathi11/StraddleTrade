"""Integration and unit tests for ShortStraddleStrategy event workflow and lifecycle."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

from nautilus_trader.cache.cache import Cache
from nautilus_trader.model.data import QuoteTick
from nautilus_trader.model.enums import OptionKind, PositionSide
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.objects import Price, Quantity
from nautilus_trader.model.position import Position

from monitoring.position_monitor import PositionMonitor
from strategies.risk_manager import OptionQuote
from strategies.short_straddle import (
    ShortStraddleConfig,
    ShortStraddleStrategy,
    StrategyState,
)
from tests.helpers import (
    make_mock_crypto_option,
    make_position_closed_event,
    make_position_opened_event,
    register_test_strategy,
)


class TestPositionMonitor:
    """Tests for PositionMonitor and startup reconciliation."""

    def test_process_position_events(self) -> None:
        pm = PositionMonitor()
        inst_id = InstrumentId.from_str("BTC-OPT.BYBIT")

        # Open event
        open_evt = make_position_opened_event(inst_id)
        pm.process_position_event(open_evt)

        # Close event
        close_evt = make_position_closed_event(inst_id)
        pm.process_position_event(close_evt)
        assert pm.is_position_open(inst_id) is False

    def test_startup_reconciliation_recovers_positions(self) -> None:
        pm = PositionMonitor()
        mock_cache = MagicMock()

        mock_pos = MagicMock(spec=Position)
        mock_pos.instrument_id = InstrumentId.from_str("BTC-24SEP27-75000-C-USDT-OPTION.BYBIT")
        mock_pos.side = PositionSide.SHORT
        mock_pos.quantity = 0.01
        mock_pos.avg_px_open = 1500.0
        mock_pos.is_open = True

        mock_cache.positions_open.return_value = [mock_pos]

        reconciled = pm.reconcile_startup_positions(mock_cache)
        assert len(reconciled) == 1
        assert pm.is_position_open(mock_pos.instrument_id) is True


class TestShortStraddleStrategy:
    """Tests for ShortStraddleStrategy workflow."""

    def test_strategy_initialization_defaults(self) -> None:
        cfg = ShortStraddleConfig(
            underlying_symbol="BTCUSDT-SPOT.BYBIT",
            trade_quantity=0.01,
            level=1,
        )
        strategy = ShortStraddleStrategy(cfg)
        register_test_strategy(strategy)
        assert strategy.workflow_state == StrategyState.INITIALIZING
        assert strategy.config.level == 1
        assert strategy.config.trade_quantity == 0.01

    def test_underlying_tick_triggers_discovery_and_dry_run_entry(self) -> None:
        cfg = ShortStraddleConfig(
            underlying_symbol="BTCUSDT-SPOT.BYBIT",
            trade_quantity=0.01,
            level=1,
            strategy_enabled=False,  # Dry run
        )
        strategy = ShortStraddleStrategy(cfg)
        cache = Cache()

        future_exp = (datetime.now(timezone.utc) + timedelta(days=7)).replace(microsecond=0)
        call_opt = make_mock_crypto_option("BTC-TEST-75000-C-USDT", OptionKind.CALL, 75000.0, future_exp)
        put_opt = make_mock_crypto_option("BTC-TEST-75000-P-USDT", OptionKind.PUT, 75000.0, future_exp)
        cache.add_instrument(call_opt)
        cache.add_instrument(put_opt)

        register_test_strategy(strategy, cache)

        # Mock Strategy subscription method
        strategy.subscribe_quote_ticks = MagicMock()  # type: ignore[method-assign]

        # Trigger on_start
        strategy.on_start()
        assert strategy.workflow_state == StrategyState.WAITING_FOR_UNDERLYING_PRICE

        # Send underlying quote tick (Mid = 75200)
        underlying_tick = QuoteTick(
            instrument_id=strategy.underlying_id,
            bid_price=Price.from_str("75190"),
            ask_price=Price.from_str("75210"),
            bid_size=Quantity.from_str("1.0"),
            ask_size=Quantity.from_str("1.0"),
            ts_event=1000,
            ts_init=1000,
        )
        strategy.on_quote_tick(underlying_tick)

        # Should have selected the 75000 strike ATM pair
        assert strategy.straddle_selection is not None
        assert strategy.straddle_selection.strike_price == 75000.0
        # In dry run mode, entry is simulated and transitions to MONITORING_STRADDLE
        assert strategy.workflow_state == StrategyState.MONITORING_STRADDLE
        assert strategy.position_state is not None
        assert strategy.position_state.entry_premium > 0

    def test_level_1_does_not_trigger_exits_on_price_move(self) -> None:
        cfg = ShortStraddleConfig(
            underlying_symbol="BTCUSDT-SPOT.BYBIT",
            trade_quantity=0.01,
            level=1,  # Level 1 = Naked short straddle, no SL/TP
            strategy_enabled=False,
        )
        strategy = ShortStraddleStrategy(cfg)
        cache = Cache()
        future_exp = (datetime.now(timezone.utc) + timedelta(days=7)).replace(microsecond=0)
        call_opt = make_mock_crypto_option("BTC-TEST-75000-C-USDT", OptionKind.CALL, 75000.0, future_exp)
        put_opt = make_mock_crypto_option("BTC-TEST-75000-P-USDT", OptionKind.PUT, 75000.0, future_exp)
        cache.add_instrument(call_opt)
        cache.add_instrument(put_opt)
        register_test_strategy(strategy, cache)

        strategy._close_straddle_positions = MagicMock()  # type: ignore[method-assign]
        strategy.subscribe_quote_ticks = MagicMock()  # type: ignore[method-assign]

        strategy.on_start()
        strategy.on_quote_tick(
            QuoteTick(
                instrument_id=strategy.underlying_id,
                bid_price=Price.from_str("75000"),
                ask_price=Price.from_str("75000"),
                bid_size=Quantity.from_str("1"),
                ask_size=Quantity.from_str("1"),
                ts_event=1000,
                ts_init=1000,
            )
        )

        # Send huge adverse price tick on Call option
        strategy.on_quote_tick(
            QuoteTick(
                instrument_id=strategy.call_leg.instrument_id,  # type: ignore[union-attr]
                bid_price=Price.from_str("99999"),
                ask_price=Price.from_str("99999"),
                bid_size=Quantity.from_str("1"),
                ask_size=Quantity.from_str("1"),
                ts_event=2000,
                ts_init=2000,
            )
        )

        # Level 1 must not trigger exit
        strategy._close_straddle_positions.assert_not_called()

    def test_level_2_triggers_stop_loss_on_cost_increase(self) -> None:
        cfg = ShortStraddleConfig(
            underlying_symbol="BTCUSDT-SPOT.BYBIT",
            trade_quantity=0.01,
            level=2,  # Level 2 = Automated SL/TP
            stop_loss_pct=0.01,
            take_profit_pct=0.02,
            strategy_enabled=False,
        )
        strategy = ShortStraddleStrategy(cfg)
        cache = Cache()
        future_exp = (datetime.now(timezone.utc) + timedelta(days=7)).replace(microsecond=0)
        call_opt = make_mock_crypto_option("BTC-TEST-75000-C-USDT", OptionKind.CALL, 75000.0, future_exp)
        put_opt = make_mock_crypto_option("BTC-TEST-75000-P-USDT", OptionKind.PUT, 75000.0, future_exp)
        cache.add_instrument(call_opt)
        cache.add_instrument(put_opt)
        register_test_strategy(strategy, cache)
        strategy.subscribe_quote_ticks = MagicMock()  # type: ignore[method-assign]

        strategy.on_start()
        # Underlying tick to enter straddle (reference price 75000)
        # In dry run mode: sim_fill_px = 75000 * 0.02 = 1500 USDT each
        # Total entry premium = 1500 * 0.01 + 1500 * 0.01 = 30.0 USDT
        # SL threshold = 30.0 * 1.01 = 30.30 USDT
        strategy.on_quote_tick(
            QuoteTick(
                instrument_id=strategy.underlying_id,
                bid_price=Price.from_str("75000"),
                ask_price=Price.from_str("75000"),
                bid_size=Quantity.from_str("1"),
                ask_size=Quantity.from_str("1"),
                ts_event=1000,
                ts_init=1000,
            )
        )

        assert strategy.position_state is not None
        now_ns = 2000
        # Call ask moves to 1600 (buyback = 16.0 + 15.0 = 31.0 >= 30.30)
        strategy.latest_put_quote = OptionQuote(bid_price=1490, ask_price=1500, bid_size=1, ask_size=1, timestamp_ns=now_ns)

        strategy.on_quote_tick(
            QuoteTick(
                instrument_id=strategy.call_leg.instrument_id,  # type: ignore[union-attr]
                bid_price=Price.from_str("1590"),
                ask_price=Price.from_str("1600"),
                bid_size=Quantity.from_str("1"),
                ask_size=Quantity.from_str("1"),
                ts_event=now_ns,
                ts_init=now_ns,
            )
        )

        # In dry run, closing orders mark exit completed and STRADDLE_CLOSED
        assert strategy.workflow_state == StrategyState.STRADDLE_CLOSED
        assert strategy.order_manager.is_exit_completed is True

    def test_level_2_triggers_take_profit_on_cost_decrease(self) -> None:
        cfg = ShortStraddleConfig(
            underlying_symbol="BTCUSDT-SPOT.BYBIT",
            trade_quantity=0.01,
            level=2,
            stop_loss_pct=0.01,
            take_profit_pct=0.02,
            strategy_enabled=False,
        )
        strategy = ShortStraddleStrategy(cfg)
        cache = Cache()
        future_exp = (datetime.now(timezone.utc) + timedelta(days=7)).replace(microsecond=0)
        call_opt = make_mock_crypto_option("BTC-TEST-75000-C-USDT", OptionKind.CALL, 75000.0, future_exp)
        put_opt = make_mock_crypto_option("BTC-TEST-75000-P-USDT", OptionKind.PUT, 75000.0, future_exp)
        cache.add_instrument(call_opt)
        cache.add_instrument(put_opt)
        register_test_strategy(strategy, cache)
        strategy.subscribe_quote_ticks = MagicMock()  # type: ignore[method-assign]

        strategy.on_start()
        # Underlying tick to enter straddle
        strategy.on_quote_tick(
            QuoteTick(
                instrument_id=strategy.underlying_id,
                bid_price=Price.from_str("75000"),
                ask_price=Price.from_str("75000"),
                bid_size=Quantity.from_str("1"),
                ask_size=Quantity.from_str("1"),
                ts_event=1000,
                ts_init=1000,
            )
        )

        # TP threshold: 30.0 * 0.98 = 29.40 USDT
        now_ns = 2000
        # Call ask moves down to 1400, Put ask moves down to 1400 (buyback = 14.0 + 14.0 = 28.0 <= 29.40)
        strategy.latest_put_quote = OptionQuote(bid_price=1390, ask_price=1400, bid_size=1, ask_size=1, timestamp_ns=now_ns)

        strategy.on_quote_tick(
            QuoteTick(
                instrument_id=strategy.call_leg.instrument_id,  # type: ignore[union-attr]
                bid_price=Price.from_str("1390"),
                ask_price=Price.from_str("1400"),
                bid_size=Quantity.from_str("1"),
                ask_size=Quantity.from_str("1"),
                ts_event=now_ns,
                ts_init=now_ns,
            )
        )

        assert strategy.workflow_state == StrategyState.STRADDLE_CLOSED
        assert strategy.order_manager.is_exit_completed is True
