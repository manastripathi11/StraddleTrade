"""Robust order tracking, execution state management, and duplicate-exit prevention.

Coordinates the lifecycle of entry (SELL) and exit (BUY) orders for Call and Put
straddle legs, tracks partial fills, handles rejections/cancellations, flags leg imbalances,
and enforces atomic exit locking to eliminate duplicate closing orders.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum, auto

from nautilus_trader.model.events import (
    OrderAccepted,
    OrderCanceled,
    OrderEvent,
    OrderFilled,
    OrderRejected,
    OrderSubmitted,
)

from utils.logger import get_logger

logger = get_logger("OrderManager")


class OrderRole(Enum):
    """Functional role of an order within the strategy lifecycle."""

    ENTRY = auto()  # Short sell order to establish position
    EXIT = auto()  # Buy order to cover and close position


class LegType(Enum):
    """Option contract type."""

    CALL = auto()
    PUT = auto()


class OrderState(Enum):
    """Detailed lifecycle state of an order."""

    PENDING_SUBMIT = auto()
    SUBMITTED = auto()
    ACCEPTED = auto()
    PARTIALLY_FILLED = auto()
    FILLED = auto()
    CANCELED = auto()
    REJECTED = auto()
    EXPIRED = auto()


@dataclass
class LegOrderTracker:
    """Tracks full execution lifecycle and fills for a single order leg."""

    client_order_id: str
    instrument_id_str: str
    role: OrderRole
    leg_type: LegType
    requested_quantity: float
    venue_order_id: str | None = None
    filled_quantity: float = 0.0
    avg_fill_price: float = 0.0
    status: OrderState = OrderState.PENDING_SUBMIT
    rejection_reason: str | None = None
    created_time_ns: int = field(default_factory=time.time_ns)
    last_update_ns: int = field(default_factory=time.time_ns)

    @property
    def is_terminal(self) -> bool:
        """Return True if order has reached a terminal exchange state."""
        return self.status in (
            OrderState.FILLED,
            OrderState.CANCELED,
            OrderState.REJECTED,
            OrderState.EXPIRED,
        )

    @property
    def is_fully_filled(self) -> bool:
        """Return True if order has been completely filled."""
        return self.status == OrderState.FILLED and self.filled_quantity >= self.requested_quantity

    def record_submission(self, event: OrderSubmitted) -> None:
        self.status = OrderState.SUBMITTED
        self.last_update_ns = event.ts_event

    def record_acceptance(self, event: OrderAccepted) -> None:
        self.status = OrderState.ACCEPTED
        self.venue_order_id = str(event.venue_order_id) if event.venue_order_id else None
        self.last_update_ns = event.ts_event

    def record_rejection(self, event: OrderRejected) -> None:
        self.status = OrderState.REJECTED
        self.rejection_reason = str(event.reason) if hasattr(event, "reason") else "Exchange Rejected"
        self.last_update_ns = event.ts_event

    def record_cancellation(self, event: OrderCanceled) -> None:
        self.status = OrderState.CANCELED
        self.last_update_ns = event.ts_event

    def record_fill(self, event: OrderFilled) -> None:
        fill_qty = float(event.last_qty)
        fill_px = float(event.last_px)

        new_total_qty = self.filled_quantity + fill_qty
        if new_total_qty > 0:
            # Update volume-weighted average fill price
            self.avg_fill_price = (
                (self.avg_fill_price * self.filled_quantity) + (fill_px * fill_qty)
            ) / new_total_qty

        self.filled_quantity = new_total_qty
        self.last_update_ns = event.ts_event

        if self.filled_quantity >= self.requested_quantity:
            self.status = OrderState.FILLED
        else:
            self.status = OrderState.PARTIALLY_FILLED


class OrderManager:
    """Manages order submission, tracking, imbalance detection, and duplicate exit protection."""

    def __init__(self) -> None:
        self._orders_by_id: dict[str, LegOrderTracker] = {}

        # Entry order references
        self.call_entry: LegOrderTracker | None = None
        self.put_entry: LegOrderTracker | None = None

        # Exit order references
        self.call_exit: LegOrderTracker | None = None
        self.put_exit: LegOrderTracker | None = None

        # Concurrency and duplicate exit guards
        self._exit_in_progress: bool = False
        self._exit_completed: bool = False

    def register_order(
        self,
        client_order_id: str,
        instrument_id_str: str,
        role: OrderRole,
        leg_type: LegType,
        quantity: float,
    ) -> LegOrderTracker:
        """Register a new outbound order prior to submission to track its state."""
        tracker = LegOrderTracker(
            client_order_id=client_order_id,
            instrument_id_str=instrument_id_str,
            role=role,
            leg_type=leg_type,
            requested_quantity=quantity,
        )
        self._orders_by_id[client_order_id] = tracker

        if role == OrderRole.ENTRY:
            if leg_type == LegType.CALL:
                self.call_entry = tracker
            else:
                self.put_entry = tracker
        else:
            if leg_type == LegType.CALL:
                self.call_exit = tracker
            else:
                self.put_exit = tracker

        logger.info(
            f"Registered {role.name} Order: ID={client_order_id} | Leg={leg_type.name} | "
            f"Instrument={instrument_id_str} | Qty={quantity}"
        )
        return tracker

    def get_order(self, client_order_id: str) -> LegOrderTracker | None:
        """Lookup order tracker by client order ID."""
        return self._orders_by_id.get(client_order_id)

    def process_order_event(self, event: OrderEvent) -> LegOrderTracker | None:
        """Route order lifecycle event to corresponding tracker."""
        client_order_id = str(event.client_order_id)
        tracker = self.get_order(client_order_id)
        if not tracker:
            return None

        if isinstance(event, OrderSubmitted):
            tracker.record_submission(event)
            logger.info(f"Order Submitted: {client_order_id} ({tracker.leg_type.name} {tracker.role.name})")

        elif isinstance(event, OrderAccepted):
            tracker.record_acceptance(event)
            logger.info(
                f"Order Accepted: {client_order_id} | Venue Order ID: {tracker.venue_order_id}"
            )

        elif isinstance(event, OrderRejected):
            tracker.record_rejection(event)
            logger.error(
                f"Order REJECTED: {client_order_id} ({tracker.leg_type.name} {tracker.role.name}) | Reason: {tracker.rejection_reason}"
            )

        elif isinstance(event, OrderCanceled):
            tracker.record_cancellation(event)
            logger.warning(
                f"Order CANCELED: {client_order_id} ({tracker.leg_type.name} {tracker.role.name}) | Filled: {tracker.filled_quantity}/{tracker.requested_quantity}"
            )

        elif isinstance(event, OrderFilled):
            tracker.record_fill(event)
            logger.info(
                f"Order FILL: {client_order_id} ({tracker.leg_type.name} {tracker.role.name}) | "
                f"Fill Qty={event.last_qty} @ {event.last_px} | "
                f"Total Filled={tracker.filled_quantity}/{tracker.requested_quantity} | "
                f"Avg Px={tracker.avg_fill_price:.4f} | Status={tracker.status.name}"
            )

        return tracker

    # ==========================================================================
    # ENTRY & EXIT STATE INQUIRY
    # ==========================================================================

    @property
    def are_both_entries_fully_filled(self) -> bool:
        """Verify whether both Call and Put entry legs are completely filled."""
        if not self.call_entry or not self.put_entry:
            return False
        return self.call_entry.is_fully_filled and self.put_entry.is_fully_filled

    @property
    def has_any_entry_fill(self) -> bool:
        """Return True if at least one entry leg has received a partial or full fill."""
        call_qty = self.call_entry.filled_quantity if self.call_entry else 0.0
        put_qty = self.put_entry.filled_quantity if self.put_entry else 0.0
        return (call_qty > 0.0) or (put_qty > 0.0)

    def check_leg_imbalance(self) -> tuple[bool, str]:
        """Detect asymmetric executions where one leg failed or closed prematurely.

        Returns
        -------
        tuple[bool, str]
            (has_imbalance, description)
        """
        if not self.call_entry or not self.put_entry:
            return False, "Orders not yet submitted"

        call_filled = self.call_entry.filled_quantity
        put_filled = self.put_entry.filled_quantity

        # Case 1: One leg terminal with 0 fill, other has fill
        if call_filled > 0 and put_filled == 0 and self.put_entry.is_terminal:
            msg = (
                f"CRITICAL LEG IMBALANCE: Call leg filled {call_filled} contracts, "
                f"but Put leg reached terminal state ({self.put_entry.status.name}) with 0 fills!"
            )
            return True, msg

        if put_filled > 0 and call_filled == 0 and self.call_entry.is_terminal:
            msg = (
                f"CRITICAL LEG IMBALANCE: Put leg filled {put_filled} contracts, "
                f"but Call leg reached terminal state ({self.call_entry.status.name}) with 0 fills!"
            )
            return True, msg

        # Case 2: Both terminal but partial fill quantities mismatch
        if self.call_entry.is_terminal and self.put_entry.is_terminal:
            if abs(call_filled - put_filled) > 1e-6:
                msg = (
                    f"PARTIAL FILL IMBALANCE: Call filled {call_filled} vs Put filled {put_filled}."
                )
                return True, msg

        return False, "Legs balanced"

    def get_net_open_quantities(self) -> tuple[float, float]:
        """Return net open short quantities for Call and Put legs.

        Net Qty = Entry Filled Qty - Exit Filled Qty
        """
        call_in = self.call_entry.filled_quantity if self.call_entry else 0.0
        call_out = self.call_exit.filled_quantity if self.call_exit else 0.0
        call_open = max(0.0, round(call_in - call_out, 6))

        put_in = self.put_entry.filled_quantity if self.put_entry else 0.0
        put_out = self.put_exit.filled_quantity if self.put_exit else 0.0
        put_open = max(0.0, round(put_in - put_out, 6))

        return call_open, put_open

    # ==========================================================================
    # ATOMIC EXIT LOCKING
    # ==========================================================================

    def can_trigger_exit(self) -> bool:
        """Check if an exit sequence can be initiated.

        Returns False if:
        - Exit is already in progress or completed.
        - No positions are open.
        """
        if self._exit_in_progress or self._exit_completed:
            return False

        call_open, put_open = self.get_net_open_quantities()
        return (call_open > 0) or (put_open > 0)

    def acquire_exit_lock(self) -> bool:
        """Atomically acquire the exit lock to prevent duplicate exit orders.

        Returns True if the lock was acquired, False if exit was already locked.
        """
        if not self.can_trigger_exit():
            logger.warning("Duplicate exit attempt suppressed by OrderManager lock")
            return False

        self._exit_in_progress = True
        logger.info("Acquired atomic EXIT LOCK. Closing orders permitted.")
        return True

    def mark_exit_completed(self) -> None:
        """Mark the straddle exit as fully finished."""
        self._exit_in_progress = False
        self._exit_completed = True
        logger.info("Straddle EXIT COMPLETED. All positions closed.")

    @property
    def is_exit_completed(self) -> bool:
        return self._exit_completed
