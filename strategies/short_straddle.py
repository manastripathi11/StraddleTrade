"""Production-grade BTC Short Straddle Strategy for NautilusTrader.

Implements Tier 1 (Naked Short Straddle) and Tier 2 (Automated Risk Management with
+1% Stop-Loss and -2% Take-Profit). Fully event-driven, with dynamic ATM strike selection,
asynchronous order tracking, partial-fill management, and duplicate exit protection.
"""

from __future__ import annotations

from enum import Enum, auto

from nautilus_trader.model.data import QuoteTick
from nautilus_trader.model.enums import OrderSide, TimeInForce
from nautilus_trader.model.events import (
    OrderAccepted,
    OrderCanceled,
    OrderFilled,
    OrderRejected,
    OrderSubmitted,
    PositionChanged,
    PositionClosed,
    PositionOpened,
)
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.instruments import CryptoOption
from nautilus_trader.model.objects import Quantity
from nautilus_trader.trading.strategy import Strategy, StrategyConfig

from execution.order_manager import LegType, OrderManager, OrderRole
from instruments.option_selector import (
    NoMatchingStraddleError,
    OptionSelector,
    StraddleLeg,
    StraddleSelection,
)
from monitoring.position_monitor import PositionMonitor
from strategies.risk_manager import (
    OptionQuote,
    RiskAction,
    RiskManager,
    StraddlePositionState,
)
from utils.logger import get_logger

logger = get_logger("ShortStraddleStrategy")


class StrategyState(Enum):
    """Lifecycle states of the Short Straddle strategy."""

    INITIALIZING = auto()
    WAITING_FOR_UNDERLYING_PRICE = auto()
    DISCOVERING_OPTIONS = auto()
    SUBMITTING_ENTRY_ORDERS = auto()
    MONITORING_STRADDLE = auto()
    EXITING_STRADDLE = auto()
    STRADDLE_CLOSED = auto()
    FAILED = auto()


class ShortStraddleConfig(StrategyConfig, kw_only=True):
    """Configuration parameters for ShortStraddleStrategy."""

    underlying_symbol: str = "BTCUSDT-SPOT.BYBIT"
    trade_quantity: float = 0.01
    level: int = 1
    stop_loss_pct: float = 0.01
    take_profit_pct: float = 0.02
    expiry_preference: str = "nearest"
    strategy_enabled: bool = False
    max_quote_stale_secs: float = 30.0


class ShortStraddleStrategy(Strategy):
    """Event-driven BTC At-The-Money (ATM) Short Straddle trading strategy."""

    def __init__(self, config: ShortStraddleConfig) -> None:
        super().__init__(config)

        # Underlying reference instrument ID
        self.underlying_id: InstrumentId = InstrumentId.from_str(config.underlying_symbol)

        # Selected Straddle Components
        self.straddle_selection: StraddleSelection | None = None
        self.call_leg: StraddleLeg | None = None
        self.put_leg: StraddleLeg | None = None
        self.call_instrument: CryptoOption | None = None
        self.put_instrument: CryptoOption | None = None

        # Strategy Workflow State
        self.workflow_state: StrategyState = StrategyState.INITIALIZING
        self.underlying_price: float | None = None

        # Core Components
        self.order_manager = OrderManager()
        self.position_monitor = PositionMonitor()
        self.risk_manager = RiskManager(
            stop_loss_pct=config.stop_loss_pct,
            take_profit_pct=config.take_profit_pct,
            max_quote_stale_secs=config.max_quote_stale_secs,
        )

        # Level 2 Position & Risk State
        self.position_state: StraddlePositionState | None = None
        self.latest_call_quote: OptionQuote | None = None
        self.latest_put_quote: OptionQuote | None = None

    # ==========================================================================
    # STRATEGY LIFECYCLE CALLBACKS
    # ==========================================================================

    def on_start(self) -> None:
        """Called by Nautilus engine when strategy starts."""
        logger.info(
            f"Starting ShortStraddleStrategy | Level: {self.config.level} | "
            f"Underlying: {self.config.underlying_symbol} | "
            f"Quantity: {self.config.trade_quantity} | "
            f"Mode: {'LIVE EXECUTION' if self.config.strategy_enabled else 'DRY RUN / MONITORING'}"
        )

        # Startup position reconciliation
        self.position_monitor.reconcile_startup_positions(self.cache)

        # Subscribe to market data for underlying reference instrument
        self.subscribe_quote_ticks(self.underlying_id)
        logger.info(f"Subscribed to underlying quote ticks for {self.underlying_id}")

        self.workflow_state = StrategyState.WAITING_FOR_UNDERLYING_PRICE

    def on_stop(self) -> None:
        """Called by Nautilus engine when strategy is stopped."""
        logger.info("Stopping ShortStraddleStrategy...")

        # Cancel any active pending orders before shutdown
        if self.workflow_state in (StrategyState.SUBMITTING_ENTRY_ORDERS, StrategyState.EXITING_STRADDLE):
            logger.warning("Canceling all pending orders upon strategy shutdown")
            self.cancel_all_orders(self.underlying_id)
            if self.call_leg:
                self.cancel_all_orders(self.call_leg.instrument_id)
            if self.put_leg:
                self.cancel_all_orders(self.put_leg.instrument_id)

        logger.info("ShortStraddleStrategy stopped safely.")

    # ==========================================================================
    # MARKET DATA CALLBACKS
    # ==========================================================================

    def on_quote_tick(self, tick: QuoteTick) -> None:
        """Process incoming quote tick events for underlying and option contracts."""
        # 1. Underlying reference price tick
        if tick.instrument_id == self.underlying_id:
            self._handle_underlying_tick(tick)
            return

        # 2. Call leg quote tick
        if self.call_leg and tick.instrument_id == self.call_leg.instrument_id:
            self.latest_call_quote = OptionQuote(
                bid_price=float(tick.bid_price),
                ask_price=float(tick.ask_price),
                bid_size=float(tick.bid_size),
                ask_size=float(tick.ask_size),
                timestamp_ns=tick.ts_event,
            )
            self._evaluate_risk()
            return

        # 3. Put leg quote tick
        if self.put_leg and tick.instrument_id == self.put_leg.instrument_id:
            self.latest_put_quote = OptionQuote(
                bid_price=float(tick.bid_price),
                ask_price=float(tick.ask_price),
                bid_size=float(tick.bid_size),
                ask_size=float(tick.ask_size),
                timestamp_ns=tick.ts_event,
            )
            self._evaluate_risk()
            return

    def _handle_underlying_tick(self, tick: QuoteTick) -> None:
        """Handle quote ticks from underlying reference instrument to determine ATM strike."""
        bid = float(tick.bid_price)
        ask = float(tick.ask_price)
        if bid <= 0 or ask <= 0:
            return

        mid_price = round((bid + ask) / 2.0, 2)
        self.underlying_price = mid_price

        # If waiting for reference price to select straddle contracts
        if self.workflow_state == StrategyState.WAITING_FOR_UNDERLYING_PRICE:
            logger.info(
                f"Underlying Reference Price Received: {mid_price:.2f} USDT (Bid: {bid:.2f}, Ask: {ask:.2f})"
            )
            self._discover_and_enter_straddle(mid_price)

    # ==========================================================================
    # STRADDLE DISCOVERY & ENTRY
    # ==========================================================================

    def _discover_and_enter_straddle(self, reference_price: float) -> None:
        """Discover available options, select ATM pair, subscribe, and submit entry orders."""
        self.workflow_state = StrategyState.DISCOVERING_OPTIONS

        # Retrieve instruments from Nautilus cache
        cached_instruments = self.cache.instruments()
        if not cached_instruments:
            logger.warning("No instruments available in Nautilus cache yet. Awaiting instrument cache...")
            self.workflow_state = StrategyState.WAITING_FOR_UNDERLYING_PRICE
            return

        try:
            selection = OptionSelector.select_straddle(
                instruments=cached_instruments,
                reference_price=reference_price,
                expiry_preference=self.config.expiry_preference,
            )
        except NoMatchingStraddleError as exc:
            logger.error(f"Straddle selection failed: {exc}")
            self.workflow_state = StrategyState.FAILED
            return

        self.straddle_selection = selection
        self.call_leg = selection.call_leg
        self.put_leg = selection.put_leg

        # Retrieve full instrument models
        self.call_instrument = self.cache.instrument(self.call_leg.instrument_id)
        self.put_instrument = self.cache.instrument(self.put_leg.instrument_id)

        # Subscribe to market data for both option contracts
        self.subscribe_quote_ticks(self.call_leg.instrument_id)
        self.subscribe_quote_ticks(self.put_leg.instrument_id)
        logger.info(
            f"Subscribed to Option Market Data: Call={self.call_leg.instrument_id} | Put={self.put_leg.instrument_id}"
        )

        # Transition to order submission
        self.workflow_state = StrategyState.SUBMITTING_ENTRY_ORDERS

        if not self.config.strategy_enabled:
            # DRY RUN / MONITORING MODE
            logger.warning(
                "STRATEGY_ENABLED=False (Dry-Run / Monitor Mode). Orders will NOT be submitted to exchange. "
                "Simulating initial straddle entry fills for observation..."
            )
            self._simulate_dry_run_entry(reference_price)
            return

        # LIVE ORDER SUBMISSION
        self._submit_entry_orders()

    def _submit_entry_orders(self) -> None:
        """Submit SELL orders for Call and Put straddle legs."""
        if not self.call_leg or not self.put_leg:
            return

        trade_qty_val = self.config.trade_quantity

        # 1. Submit SELL Call Order
        call_qty = (
            self.call_instrument.make_qty(trade_qty_val)
            if self.call_instrument
            else Quantity.from_str(str(trade_qty_val))
        )
        call_order = self.order_factory.market(
            instrument_id=self.call_leg.instrument_id,
            order_side=OrderSide.SELL,
            quantity=call_qty,
            time_in_force=TimeInForce.GTC,
        )
        self.order_manager.register_order(
            client_order_id=str(call_order.client_order_id),
            instrument_id_str=str(self.call_leg.instrument_id),
            role=OrderRole.ENTRY,
            leg_type=LegType.CALL,
            quantity=trade_qty_val,
        )
        self.submit_order(call_order)
        logger.info(
            f"Submitted SELL CALL Order: ClientOrderID={call_order.client_order_id} | "
            f"Instrument={self.call_leg.instrument_id} | Qty={call_qty}"
        )

        # 2. Submit SELL Put Order
        put_qty = (
            self.put_instrument.make_qty(trade_qty_val)
            if self.put_instrument
            else Quantity.from_str(str(trade_qty_val))
        )
        put_order = self.order_factory.market(
            instrument_id=self.put_leg.instrument_id,
            order_side=OrderSide.SELL,
            quantity=put_qty,
            time_in_force=TimeInForce.GTC,
        )
        self.order_manager.register_order(
            client_order_id=str(put_order.client_order_id),
            instrument_id_str=str(self.put_leg.instrument_id),
            role=OrderRole.ENTRY,
            leg_type=LegType.PUT,
            quantity=trade_qty_val,
        )
        self.submit_order(put_order)
        logger.info(
            f"Submitted SELL PUT Order: ClientOrderID={put_order.client_order_id} | "
            f"Instrument={self.put_leg.instrument_id} | Qty={put_qty}"
        )

    def _simulate_dry_run_entry(self, reference_price: float) -> None:
        """Simulate realistic entry fills for dry-run observation mode."""
        if not self.call_leg or not self.put_leg:
            return

        sim_call_fill_px = max(50.0, round(reference_price * 0.02, 2))
        sim_put_fill_px = max(50.0, round(reference_price * 0.02, 2))
        trade_qty = self.config.trade_quantity

        # Populate order manager mock trackers
        call_tracker = self.order_manager.register_order(
            client_order_id="DRY-RUN-CALL-ENTRY",
            instrument_id_str=str(self.call_leg.instrument_id),
            role=OrderRole.ENTRY,
            leg_type=LegType.CALL,
            quantity=trade_qty,
        )
        call_tracker.filled_quantity = trade_qty
        call_tracker.avg_fill_price = sim_call_fill_px
        call_tracker.status = call_tracker.status.FILLED

        put_tracker = self.order_manager.register_order(
            client_order_id="DRY-RUN-PUT-ENTRY",
            instrument_id_str=str(self.put_leg.instrument_id),
            role=OrderRole.ENTRY,
            leg_type=LegType.PUT,
            quantity=trade_qty,
        )
        put_tracker.filled_quantity = trade_qty
        put_tracker.avg_fill_price = sim_put_fill_px
        put_tracker.status = put_tracker.status.FILLED

        self.position_state = self.risk_manager.create_position_state(
            call_fill_price=sim_call_fill_px,
            call_quantity=trade_qty,
            put_fill_price=sim_put_fill_px,
            put_quantity=trade_qty,
            call_multiplier=self.call_leg.multiplier,
            put_multiplier=self.put_leg.multiplier,
        )
        self.workflow_state = StrategyState.MONITORING_STRADDLE
        logger.info("Dry-run straddle entered. Now monitoring market movements...")

    # ==========================================================================
    # ORDER EVENT CALLBACKS
    # ==========================================================================

    def on_order_submitted(self, event: OrderSubmitted) -> None:
        self.order_manager.process_order_event(event)

    def on_order_accepted(self, event: OrderAccepted) -> None:
        self.order_manager.process_order_event(event)

    def on_order_rejected(self, event: OrderRejected) -> None:
        self.order_manager.process_order_event(event)
        self._check_for_imbalances()

    def on_order_canceled(self, event: OrderCanceled) -> None:
        self.order_manager.process_order_event(event)
        self._check_for_imbalances()

    def on_order_filled(self, event: OrderFilled) -> None:
        """Handle execution fills for entry and exit legs."""
        tracker = self.order_manager.process_order_event(event)
        if not tracker:
            return

        # Entry Leg Filled
        if tracker.role == OrderRole.ENTRY:
            if self.order_manager.are_both_entries_fully_filled:
                call_tracker = self.order_manager.call_entry
                put_tracker = self.order_manager.put_entry
                if call_tracker and put_tracker and self.call_leg and self.put_leg:
                    self.position_state = self.risk_manager.create_position_state(
                        call_fill_price=call_tracker.avg_fill_price,
                        call_quantity=call_tracker.filled_quantity,
                        put_fill_price=put_tracker.avg_fill_price,
                        put_quantity=put_tracker.filled_quantity,
                        call_multiplier=self.call_leg.multiplier,
                        put_multiplier=self.put_leg.multiplier,
                    )
                    self.workflow_state = StrategyState.MONITORING_STRADDLE
                    logger.info(
                        f"Both Entry Legs FILLED | Straddle Open | Combined Premium: {self.position_state.entry_premium:.4f} USDT"
                    )

        # Exit Leg Filled
        elif tracker.role == OrderRole.EXIT:
            call_open, put_open = self.order_manager.get_net_open_quantities()
            if call_open == 0.0 and put_open == 0.0:
                self.order_manager.mark_exit_completed()
                self.workflow_state = StrategyState.STRADDLE_CLOSED
                logger.info("ALL STRADDLE POSITIONS SUCCESSFULLY CLOSED AND CONFIRMED.")

    def _check_for_imbalances(self) -> None:
        """Check for and alert on execution leg imbalances."""
        has_imbalance, reason = self.order_manager.check_leg_imbalance()
        if has_imbalance:
            logger.error(f"EXECUTION ALERT: {reason}")

    # ==========================================================================
    # POSITION EVENT CALLBACKS
    # ==========================================================================

    def on_position_opened(self, event: PositionOpened) -> None:
        self.position_monitor.process_position_event(event)

    def on_position_changed(self, event: PositionChanged) -> None:
        self.position_monitor.process_position_event(event)

    def on_position_closed(self, event: PositionClosed) -> None:
        self.position_monitor.process_position_event(event)

    # ==========================================================================
    # LEVEL 2 RISK MANAGEMENT EVALUATION
    # ==========================================================================

    def _evaluate_risk(self) -> None:
        """Evaluate Level 2 risk management rules on latest option market quotes."""
        # Level 1 mode does not execute automated SL/TP exits
        if self.config.level < 2:
            return

        if self.workflow_state != StrategyState.MONITORING_STRADDLE or self.position_state is None:
            return

        current_time_ns: int | None = None
        if self.latest_call_quote and self.latest_put_quote:
            import time

            wall_clock_ns = time.time_ns()
            latest_quote_ns = max(
                self.latest_call_quote.timestamp_ns, self.latest_put_quote.timestamp_ns
            )
            # If historical/simulated timestamps are used (e.g. In unit tests or backtests)
            if abs(wall_clock_ns - latest_quote_ns) > int(3600 * 1e9):
                current_time_ns = latest_quote_ns

        eval_result = self.risk_manager.evaluate(
            state=self.position_state,
            call_quote=self.latest_call_quote,
            put_quote=self.latest_put_quote,
            current_time_ns=current_time_ns,
        )

        if eval_result.action in (RiskAction.STOP_LOSS_TRIGGERED, RiskAction.TAKE_PROFIT_TRIGGERED):
            logger.warning(
                f"RISK TRIGGER ACTIVATED: {eval_result.action.name} | Reason: {eval_result.reason}"
            )
            self._close_straddle_positions(reason=eval_result.action.name)

    def _close_straddle_positions(self, reason: str) -> None:
        """Execute atomic exit sequence to buy back both open option legs."""
        # Atomic lock to prevent duplicate exit orders
        if not self.order_manager.acquire_exit_lock():
            return

        self.workflow_state = StrategyState.EXITING_STRADDLE
        call_open, put_open = self.order_manager.get_net_open_quantities()

        logger.info(
            f"Initiating Straddle Exit ({reason}) | Buying back: Call={call_open} contracts, Put={put_open} contracts"
        )

        if not self.config.strategy_enabled:
            logger.warning("Dry-Run mode: Simulating exit orders execution...")
            self.order_manager.mark_exit_completed()
            self.workflow_state = StrategyState.STRADDLE_CLOSED
            logger.info("Dry-Run straddle positions marked closed.")
            return

        # 1. Close Call Leg
        if call_open > 0 and self.call_leg:
            call_qty = (
                self.call_instrument.make_qty(call_open)
                if self.call_instrument
                else Quantity.from_str(str(call_open))
            )
            call_exit_order = self.order_factory.market(
                instrument_id=self.call_leg.instrument_id,
                order_side=OrderSide.BUY,
                quantity=call_qty,
                reduce_only=True,
                time_in_force=TimeInForce.GTC,
            )
            self.order_manager.register_order(
                client_order_id=str(call_exit_order.client_order_id),
                instrument_id_str=str(self.call_leg.instrument_id),
                role=OrderRole.EXIT,
                leg_type=LegType.CALL,
                quantity=call_open,
            )
            self.submit_order(call_exit_order)
            logger.info(f"Submitted BUY EXIT CALL Order: {call_exit_order.client_order_id}")

        # 2. Close Put Leg
        if put_open > 0 and self.put_leg:
            put_qty = (
                self.put_instrument.make_qty(put_open)
                if self.put_instrument
                else Quantity.from_str(str(put_open))
            )
            put_exit_order = self.order_factory.market(
                instrument_id=self.put_leg.instrument_id,
                order_side=OrderSide.BUY,
                quantity=put_qty,
                reduce_only=True,
                time_in_force=TimeInForce.GTC,
            )
            self.order_manager.register_order(
                client_order_id=str(put_exit_order.client_order_id),
                instrument_id_str=str(self.put_leg.instrument_id),
                role=OrderRole.EXIT,
                leg_type=LegType.PUT,
                quantity=put_open,
            )
            self.submit_order(put_exit_order)
            logger.info(f"Submitted BUY EXIT PUT Order: {put_exit_order.client_order_id}")
