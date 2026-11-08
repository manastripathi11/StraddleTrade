"""Automated Risk Management for BTC Short Straddle Strategy.

Implements real-time combined buyback valuation using ask-side quotes,
dynamically tracks entry premium, and enforces strict Stop-Loss (+1% cost increase)
and Take-Profit (-2% cost reduction) triggers with comprehensive edge-case handling.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from enum import Enum, auto

from utils.logger import get_logger

logger = get_logger("RiskManager")


class RiskAction(Enum):
    """Actions resulting from risk evaluation."""

    NO_ACTION = auto()
    STOP_LOSS_TRIGGERED = auto()
    TAKE_PROFIT_TRIGGERED = auto()
    DATA_MISSING = auto()
    DATA_STALE = auto()


@dataclass(frozen=True)
class OptionQuote:
    """Best bid/ask quote snapshot for an option leg."""

    bid_price: float
    ask_price: float
    bid_size: float
    ask_size: float
    timestamp_ns: int

    @property
    def is_valid_ask(self) -> bool:
        """Verify whether ask price is positive and non-zero."""
        return self.ask_price > 0.0


@dataclass(frozen=True)
class StraddlePositionState:
    """Consolidated state of the short straddle position."""

    call_fill_price: float
    call_quantity: float
    put_fill_price: float
    put_quantity: float
    entry_premium: float
    stop_loss_threshold: float
    take_profit_threshold: float
    call_multiplier: float = 1.0
    put_multiplier: float = 1.0
    stop_loss_pct: float = 0.01
    take_profit_pct: float = 0.02

    @property
    def is_valid(self) -> bool:
        return self.entry_premium > 0 and self.call_quantity > 0 and self.put_quantity > 0


@dataclass(frozen=True)
class RiskEvaluation:
    """Detailed result of a risk management evaluation cycle."""

    action: RiskAction
    buyback_cost: float | None
    entry_premium: float
    stop_loss_threshold: float
    take_profit_threshold: float
    pnl_amount: float | None
    pnl_percentage: float | None
    reason: str


class RiskManager:
    """Evaluates real-time combined straddle buyback value against risk boundaries."""

    def __init__(
        self,
        stop_loss_pct: float = 0.01,
        take_profit_pct: float = 0.02,
        max_quote_stale_secs: float = 30.0,
    ) -> None:
        """Initialize RiskManager with risk thresholds.

        Parameters
        ----------
        stop_loss_pct : float
            Stop-loss trigger percentage increase in buyback cost (default 0.01 = 1%).
        take_profit_pct : float
            Take-profit trigger percentage decrease in buyback cost (default 0.02 = 2%).
        max_quote_stale_secs : float
            Maximum allowable quote age in seconds before flagging as stale.
        """
        if stop_loss_pct <= 0:
            raise ValueError(f"stop_loss_pct must be positive, got {stop_loss_pct}")
        if take_profit_pct <= 0:
            raise ValueError(f"take_profit_pct must be positive, got {take_profit_pct}")
        if max_quote_stale_secs <= 0:
            raise ValueError(f"max_quote_stale_secs must be positive, got {max_quote_stale_secs}")

        self.stop_loss_pct = stop_loss_pct
        self.take_profit_pct = take_profit_pct
        self.max_quote_stale_secs = max_quote_stale_secs

    @staticmethod
    def calculate_entry_premium(
        call_fill_price: float,
        call_quantity: float,
        put_fill_price: float,
        put_quantity: float,
        call_multiplier: float = 1.0,
        put_multiplier: float = 1.0,
    ) -> float:
        """Calculate combined entry premium received from selling both legs.

        Entry Premium = (Call Fill Px * Call Qty * Multiplier) + (Put Fill Px * Put Qty * Multiplier)
        """
        if call_fill_price < 0 or put_fill_price < 0:
            raise ValueError("Option fill prices cannot be negative")
        if call_quantity <= 0 or put_quantity <= 0:
            raise ValueError("Option quantities must be strictly positive")

        call_premium = call_fill_price * call_quantity * call_multiplier
        put_premium = put_fill_price * put_quantity * put_multiplier
        return round(call_premium + put_premium, 6)

    @staticmethod
    def calculate_buyback_cost(
        call_ask_price: float,
        call_quantity: float,
        put_ask_price: float,
        put_quantity: float,
        call_multiplier: float = 1.0,
        put_multiplier: float = 1.0,
    ) -> float:
        """Calculate current combined buyback cost using ASK-side quotes.

        Combined Buyback Cost = (Call Ask Px * Call Qty * Multiplier) + (Put Ask Px * Put Qty * Multiplier)
        """
        if call_ask_price < 0 or put_ask_price < 0:
            raise ValueError("Option ask prices cannot be negative")

        call_cost = call_ask_price * call_quantity * call_multiplier
        put_cost = put_ask_price * put_quantity * put_multiplier
        return round(call_cost + put_cost, 6)

    def calculate_thresholds(self, entry_premium: float) -> tuple[float, float]:
        """Compute the Stop-Loss and Take-Profit monetary thresholds.

        Stop Loss Threshold = Entry Premium * (1 + stop_loss_pct)
        Take Profit Threshold = Entry Premium * (1 - take_profit_pct)
        """
        if entry_premium <= 0:
            raise ValueError(f"Entry premium must be positive to compute thresholds, got {entry_premium}")

        stop_loss_threshold = round(entry_premium * (1.0 + self.stop_loss_pct), 6)
        take_profit_threshold = round(entry_premium * (1.0 - self.take_profit_pct), 6)
        return stop_loss_threshold, take_profit_threshold

    def create_position_state(
        self,
        call_fill_price: float,
        call_quantity: float,
        put_fill_price: float,
        put_quantity: float,
        call_multiplier: float = 1.0,
        put_multiplier: float = 1.0,
    ) -> StraddlePositionState:
        """Construct the immutable position state and pre-compute SL/TP thresholds."""
        entry_premium = self.calculate_entry_premium(
            call_fill_price=call_fill_price,
            call_quantity=call_quantity,
            put_fill_price=put_fill_price,
            put_quantity=put_quantity,
            call_multiplier=call_multiplier,
            put_multiplier=put_multiplier,
        )
        sl_threshold, tp_threshold = self.calculate_thresholds(entry_premium)

        logger.info(
            f"Straddle Position Established | Combined Entry Premium: {entry_premium:.4f} USDT | "
            f"Stop-Loss Threshold (+{self.stop_loss_pct * 100:.1f}%): {sl_threshold:.4f} USDT | "
            f"Take-Profit Threshold (-{self.take_profit_pct * 100:.1f}%): {tp_threshold:.4f} USDT"
        )

        return StraddlePositionState(
            call_fill_price=call_fill_price,
            call_quantity=call_quantity,
            put_fill_price=put_fill_price,
            put_quantity=put_quantity,
            entry_premium=entry_premium,
            stop_loss_threshold=sl_threshold,
            take_profit_threshold=tp_threshold,
            call_multiplier=call_multiplier,
            put_multiplier=put_multiplier,
            stop_loss_pct=self.stop_loss_pct,
            take_profit_pct=self.take_profit_pct,
        )

    def evaluate(
        self,
        state: StraddlePositionState,
        call_quote: OptionQuote | None,
        put_quote: OptionQuote | None,
        current_time_ns: int | None = None,
    ) -> RiskEvaluation:
        """Evaluate real-time quotes against Stop-Loss and Take-Profit conditions.

        Parameters
        ----------
        state : StraddlePositionState
            The active straddle entry state and thresholds.
        call_quote : OptionQuote | None
            Latest quote tick for the Call leg.
        put_quote : OptionQuote | None
            Latest quote tick for the Put leg.
        current_time_ns : int | None
            Current timestamp in nanoseconds for staleness checks.

        Returns
        -------
        RiskEvaluation
            Evaluation decision with action and detailed metrics.
        """
        # Guard: Ensure quotes exist
        if call_quote is None or put_quote is None:
            return RiskEvaluation(
                action=RiskAction.DATA_MISSING,
                buyback_cost=None,
                entry_premium=state.entry_premium,
                stop_loss_threshold=state.stop_loss_threshold,
                take_profit_threshold=state.take_profit_threshold,
                pnl_amount=None,
                pnl_percentage=None,
                reason="One or both option leg quotes are missing from market data feed",
            )

        # Guard: Ensure valid positive ask quotes
        if not call_quote.is_valid_ask or not put_quote.is_valid_ask:
            return RiskEvaluation(
                action=RiskAction.DATA_MISSING,
                buyback_cost=None,
                entry_premium=state.entry_premium,
                stop_loss_threshold=state.stop_loss_threshold,
                take_profit_threshold=state.take_profit_threshold,
                pnl_amount=None,
                pnl_percentage=None,
                reason=(
                    f"Non-positive ask price detected: Call Ask={call_quote.ask_price}, "
                    f"Put Ask={put_quote.ask_price}. Cannot evaluate buyback cost."
                ),
            )

        # Guard: Check for stale market data
        now_ns = current_time_ns if current_time_ns is not None else time.time_ns()
        max_stale_ns = int(self.max_quote_stale_secs * 1e9)

        call_age_secs = (now_ns - call_quote.timestamp_ns) / 1e9
        put_age_secs = (now_ns - put_quote.timestamp_ns) / 1e9

        if (now_ns - call_quote.timestamp_ns) > max_stale_ns or (now_ns - put_quote.timestamp_ns) > max_stale_ns:
            return RiskEvaluation(
                action=RiskAction.DATA_STALE,
                buyback_cost=None,
                entry_premium=state.entry_premium,
                stop_loss_threshold=state.stop_loss_threshold,
                take_profit_threshold=state.take_profit_threshold,
                pnl_amount=None,
                pnl_percentage=None,
                reason=(
                    f"Option quotes are stale (Call age: {call_age_secs:.1f}s, "
                    f"Put age: {put_age_secs:.1f}s > max {self.max_quote_stale_secs}s)"
                ),
            )

        # Calculate current combined buyback cost
        buyback_cost = self.calculate_buyback_cost(
            call_ask_price=call_quote.ask_price,
            call_quantity=state.call_quantity,
            put_ask_price=put_quote.ask_price,
            put_quantity=state.put_quantity,
            call_multiplier=state.call_multiplier,
            put_multiplier=state.put_multiplier,
        )

        # For a short straddle:
        # PnL = Initial Premium Received - Current Buyback Cost
        pnl_amount = round(state.entry_premium - buyback_cost, 6)
        pnl_pct = round((pnl_amount / state.entry_premium) * 100, 4)

        # Check Stop-Loss (+1% increase in buyback cost)
        if buyback_cost >= state.stop_loss_threshold:
            cost_increase_pct = ((buyback_cost - state.entry_premium) / state.entry_premium) * 100
            msg = (
                f"STOP-LOSS TRIGGERED! Buyback Cost ({buyback_cost:.4f} USDT) >= "
                f"Threshold ({state.stop_loss_threshold:.4f} USDT) | "
                f"Cost increased by {cost_increase_pct:.2f}% (Limit: +{state.stop_loss_pct * 100:.1f}%) | "
                f"PnL: {pnl_amount:.4f} USDT ({pnl_pct:.2f}%)"
            )
            logger.warning(msg)
            return RiskEvaluation(
                action=RiskAction.STOP_LOSS_TRIGGERED,
                buyback_cost=buyback_cost,
                entry_premium=state.entry_premium,
                stop_loss_threshold=state.stop_loss_threshold,
                take_profit_threshold=state.take_profit_threshold,
                pnl_amount=pnl_amount,
                pnl_percentage=pnl_pct,
                reason=msg,
            )

        # Check Take-Profit (-2% decrease in buyback cost)
        if buyback_cost <= state.take_profit_threshold:
            cost_decrease_pct = ((state.entry_premium - buyback_cost) / state.entry_premium) * 100
            msg = (
                f"TAKE-PROFIT TRIGGERED! Buyback Cost ({buyback_cost:.4f} USDT) <= "
                f"Threshold ({state.take_profit_threshold:.4f} USDT) | "
                f"Cost decreased by {cost_decrease_pct:.2f}% (Target: -{state.take_profit_pct * 100:.1f}%) | "
                f"PnL: +{pnl_amount:.4f} USDT (+{pnl_pct:.2f}%)"
            )
            logger.info(msg)
            return RiskEvaluation(
                action=RiskAction.TAKE_PROFIT_TRIGGERED,
                buyback_cost=buyback_cost,
                entry_premium=state.entry_premium,
                stop_loss_threshold=state.stop_loss_threshold,
                take_profit_threshold=state.take_profit_threshold,
                pnl_amount=pnl_amount,
                pnl_percentage=pnl_pct,
                reason=msg,
            )

        # Within acceptable risk band
        return RiskEvaluation(
            action=RiskAction.NO_ACTION,
            buyback_cost=buyback_cost,
            entry_premium=state.entry_premium,
            stop_loss_threshold=state.stop_loss_threshold,
            take_profit_threshold=state.take_profit_threshold,
            pnl_amount=pnl_amount,
            pnl_percentage=pnl_pct,
            reason=(
                f"Monitoring Straddle: Buyback Cost={buyback_cost:.4f} USDT, "
                f"PnL={pnl_amount:+.4f} USDT ({pnl_pct:+.2f}%) within [TP: {state.take_profit_threshold:.4f}, SL: {state.stop_loss_threshold:.4f}]"
            ),
        )
