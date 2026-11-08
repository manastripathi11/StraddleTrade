"""Real-time position monitoring, lifecycle tracking, and startup reconciliation.

Maintains an accurate view of open option positions using NautilusTrader cache
and position events, ensuring positions are only marked closed upon confirmed
execution events rather than speculative order submissions.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from nautilus_trader.model.events import (
    PositionChanged,
    PositionClosed,
    PositionEvent,
    PositionOpened,
)
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.position import Position

from utils.logger import get_logger

if TYPE_CHECKING:
    from nautilus_trader.cache.cache import Cache

logger = get_logger("PositionMonitor")


class PositionMonitor:
    """Monitors live positions and performs startup reconciliation."""

    def __init__(self) -> None:
        self._positions_by_instrument: dict[str, Position] = {}
        self._closed_positions: set[str] = set()

    def process_position_event(self, event: PositionEvent) -> None:
        """Update internal position cache when Nautilus position events arrive."""
        inst_id_str = str(event.instrument_id)
        side_val = getattr(event, "side", getattr(event, "position_side", "N/A"))

        if isinstance(event, PositionOpened):
            logger.info(
                f"Position OPENED: {inst_id_str} | Side: {side_val} | Qty: {getattr(event, 'quantity', 'N/A')}"
            )
            # Replaced when position object is updated in cache

        elif isinstance(event, PositionChanged):
            logger.info(
                f"Position CHANGED: {inst_id_str} | Side: {side_val} | Qty: {getattr(event, 'quantity', 'N/A')}"
            )

        elif isinstance(event, PositionClosed):
            logger.info(
                f"Position CONFIRMED CLOSED: {inst_id_str} | Realized PnL: {getattr(event, 'realized_pnl', 'N/A')}"
            )
            self._positions_by_instrument.pop(inst_id_str, None)
            self._closed_positions.add(inst_id_str)

    def update_from_cache(self, cache: Cache | None, call_id: InstrumentId | None, put_id: InstrumentId | None) -> None:
        """Sync live position state directly from Nautilus cache."""
        if cache is None:
            return

        if call_id:
            call_pos = cache.position(call_id)
            if call_pos and call_pos.is_open:
                self._positions_by_instrument[str(call_id)] = call_pos
            elif str(call_id) in self._positions_by_instrument:
                self._positions_by_instrument.pop(str(call_id), None)

        if put_id:
            put_pos = cache.position(put_id)
            if put_pos and put_pos.is_open:
                self._positions_by_instrument[str(put_id)] = put_pos
            elif str(put_id) in self._positions_by_instrument:
                self._positions_by_instrument.pop(str(put_id), None)

    def is_position_open(self, instrument_id: InstrumentId) -> bool:
        """Check if an open position currently exists for the given instrument."""
        pos = self._positions_by_instrument.get(str(instrument_id))
        return pos is not None and pos.is_open

    def get_position(self, instrument_id: InstrumentId) -> Position | None:
        return self._positions_by_instrument.get(str(instrument_id))

    def are_both_legs_closed(self, call_id: InstrumentId, put_id: InstrumentId) -> bool:
        """Verify whether both straddle option legs are confirmed closed."""
        call_open = self.is_position_open(call_id)
        put_open = self.is_position_open(put_id)
        return not call_open and not put_open

    def reconcile_startup_positions(self, cache: Cache | None) -> list[Position]:
        """Scan Nautilus cache on startup for open option positions.

        Handles strategy restart with existing open positions.
        """
        if cache is None:
            return []

        open_positions = cache.positions_open()
        reconciled: list[Position] = []

        for pos in open_positions:
            inst_str = str(pos.instrument_id)
            if "OPTION" in inst_str or "BTC-" in inst_str:
                self._positions_by_instrument[inst_str] = pos
                reconciled.append(pos)
                logger.warning(
                    f"STARTUP RECONCILIATION: Recovered pre-existing open option position: "
                    f"{inst_str} | Side: {pos.side} | Qty: {pos.quantity} | Avg Open Px: {pos.avg_px_open}"
                )

        if not reconciled:
            logger.info("Startup reconciliation clean: No orphaned open option positions found.")

        return reconciled
