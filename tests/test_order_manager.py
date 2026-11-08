"""Unit tests for OrderManager execution lifecycle, partial fills, and duplicate exit guards."""

from __future__ import annotations

from execution.order_manager import (
    LegOrderTracker,
    LegType,
    OrderManager,
    OrderRole,
    OrderState,
)
from tests.helpers import (
    make_order_accepted_event,
    make_order_canceled_event,
    make_order_filled_event,
    make_order_rejected_event,
    make_order_submitted_event,
)


class TestOrderManager:
    """Tests for OrderManager and LegOrderTracker."""

    def test_order_lifecycle_full_fill(self) -> None:
        om = OrderManager()
        tracker = om.register_order("C1", "BTC-C", OrderRole.ENTRY, LegType.CALL, 0.01)
        assert tracker.status == OrderState.PENDING_SUBMIT

        # Submitted
        sub_evt = make_order_submitted_event("C1")
        om.process_order_event(sub_evt)
        assert tracker.status == OrderState.SUBMITTED

        # Accepted
        acc_evt = make_order_accepted_event("C1", "BYBIT-999")
        om.process_order_event(acc_evt)
        assert tracker.status == OrderState.ACCEPTED
        assert tracker.venue_order_id == "BYBIT-999"

        # Filled
        fill_evt = make_order_filled_event("C1", "0.01", "1500")
        om.process_order_event(fill_evt)
        assert tracker.status == OrderState.FILLED
        assert tracker.filled_quantity == 0.01
        assert tracker.avg_fill_price == 1500.0
        assert tracker.is_fully_filled

    def test_partial_fills_and_weighted_average_price(self) -> None:
        tracker = LegOrderTracker(
            client_order_id="P1",
            instrument_id_str="BTC-P",
            role=OrderRole.ENTRY,
            leg_type=LegType.PUT,
            requested_quantity=0.02,
        )

        # First fill: 0.01 @ 1000
        fill1 = make_order_filled_event("P1", "0.01", "1000")
        tracker.record_fill(fill1)
        assert tracker.status == OrderState.PARTIALLY_FILLED
        assert tracker.filled_quantity == 0.01
        assert tracker.avg_fill_price == 1000.0

        # Second fill: 0.01 @ 1100
        fill2 = make_order_filled_event("P1", "0.01", "1100")
        tracker.record_fill(fill2)
        assert tracker.status == OrderState.FILLED
        assert tracker.filled_quantity == 0.02
        # (1000 * 0.01 + 1100 * 0.01) / 0.02 = 1050.0
        assert tracker.avg_fill_price == 1050.0

    def test_rejection_handling(self) -> None:
        om = OrderManager()
        tracker = om.register_order("C_REJ", "BTC-C", OrderRole.ENTRY, LegType.CALL, 0.01)
        rej_evt = make_order_rejected_event("C_REJ", "Account restricted")
        om.process_order_event(rej_evt)

        assert tracker.status == OrderState.REJECTED
        assert tracker.rejection_reason == "Account restricted"
        assert tracker.is_terminal

    def test_cancellation_handling(self) -> None:
        om = OrderManager()
        tracker = om.register_order("C_CAN", "BTC-C", OrderRole.ENTRY, LegType.CALL, 0.01)
        can_evt = make_order_canceled_event("C_CAN")
        om.process_order_event(can_evt)

        assert tracker.status == OrderState.CANCELED
        assert tracker.is_terminal

    def test_leg_imbalance_one_leg_rejected(self) -> None:
        om = OrderManager()
        om.register_order("C1", "BTC-C", OrderRole.ENTRY, LegType.CALL, 0.01)
        om.register_order("P1", "BTC-P", OrderRole.ENTRY, LegType.PUT, 0.01)

        # Call fills
        om.process_order_event(make_order_filled_event("C1", "0.01", "1200"))
        # Put gets rejected
        om.process_order_event(make_order_rejected_event("P1", "Post-only failed"))

        has_imbalance, reason = om.check_leg_imbalance()
        assert has_imbalance is True
        assert "CRITICAL LEG IMBALANCE" in reason
        assert "Call leg filled 0.01" in reason

    def test_duplicate_exit_prevention(self) -> None:
        om = OrderManager()
        om.register_order("C_ENT", "BTC-C", OrderRole.ENTRY, LegType.CALL, 0.01)
        om.register_order("P_ENT", "BTC-P", OrderRole.ENTRY, LegType.PUT, 0.01)

        # Before fills: cannot exit
        assert om.can_trigger_exit() is False

        # Fill entry legs
        om.process_order_event(make_order_filled_event("C_ENT", "0.01", "1000"))
        om.process_order_event(make_order_filled_event("P_ENT", "0.01", "1000"))

        assert om.can_trigger_exit() is True

        # First acquisition of exit lock succeeds
        lock_acquired_1 = om.acquire_exit_lock()
        assert lock_acquired_1 is True

        # Second concurrent acquisition is blocked
        lock_acquired_2 = om.acquire_exit_lock()
        assert lock_acquired_2 is False

        # Third attempt also blocked
        assert om.can_trigger_exit() is False

        # After exit completed
        om.mark_exit_completed()
        assert om.is_exit_completed is True
        assert om.can_trigger_exit() is False

    def test_net_open_quantities(self) -> None:
        om = OrderManager()
        om.register_order("C_ENT", "BTC-C", OrderRole.ENTRY, LegType.CALL, 0.01)
        om.register_order("P_ENT", "BTC-P", OrderRole.ENTRY, LegType.PUT, 0.01)
        om.process_order_event(make_order_filled_event("C_ENT", "0.01", "1000"))
        om.process_order_event(make_order_filled_event("P_ENT", "0.01", "1000"))

        assert om.get_net_open_quantities() == (0.01, 0.01)

        # Register and fill Call exit order
        om.register_order("C_EXT", "BTC-C", OrderRole.EXIT, LegType.CALL, 0.01)
        om.process_order_event(make_order_filled_event("C_EXT", "0.01", "1050"))

        assert om.get_net_open_quantities() == (0.0, 0.01)
