"""Unit tests for Automated Risk Management calculations, buyback cost valuation, and SL/TP triggers."""

from __future__ import annotations

import time

from strategies.risk_manager import (
    OptionQuote,
    RiskAction,
    RiskManager,
)


class TestRiskManager:
    """Tests for entry premium, buyback cost, and SL/TP threshold logic."""

    def setup_method(self) -> None:
        self.rm = RiskManager(
            stop_loss_pct=0.01,
            take_profit_pct=0.02,
            max_quote_stale_secs=30.0,
        )

    def test_calculate_entry_premium_standard(self) -> None:
        # Call sold at 1500, Put sold at 1200, qty 0.01 each
        # (1500 * 0.01) + (1200 * 0.01) = 15.0 + 12.0 = 27.0 USDT
        premium = self.rm.calculate_entry_premium(
            call_fill_price=1500.0,
            call_quantity=0.01,
            put_fill_price=1200.0,
            put_quantity=0.01,
        )
        assert premium == 27.0

    def test_calculate_entry_premium_with_multiplier(self) -> None:
        premium = self.rm.calculate_entry_premium(
            call_fill_price=100.0,
            call_quantity=2.0,
            put_fill_price=100.0,
            put_quantity=2.0,
            call_multiplier=0.5,
            put_multiplier=0.5,
        )
        # (100 * 2 * 0.5) + (100 * 2 * 0.5) = 100.0 + 100.0 = 200.0
        assert premium == 200.0

    def test_calculate_buyback_cost_uses_ask_side(self) -> None:
        # Call ask 1600, Put ask 1100, qty 0.01
        # (1600 * 0.01) + (1100 * 0.01) = 16.0 + 11.0 = 27.0
        cost = self.rm.calculate_buyback_cost(
            call_ask_price=1600.0,
            call_quantity=0.01,
            put_ask_price=1100.0,
            put_quantity=0.01,
        )
        assert cost == 27.0

    def test_calculate_thresholds_exact_formula(self) -> None:
        # Stop-Loss: Entry * 1.01 | Take-Profit: Entry * 0.98
        entry_premium = 100.0
        sl_thresh, tp_thresh = self.rm.calculate_thresholds(entry_premium)
        assert sl_thresh == 101.0
        assert tp_thresh == 98.0

    def test_stop_loss_trigger(self) -> None:
        # Entry: Call 1000, Put 1000, qty 0.01 -> Entry Premium = 20.0
        # SL Threshold = 20.0 * 1.01 = 20.20
        state = self.rm.create_position_state(
            call_fill_price=1000.0,
            call_quantity=0.01,
            put_fill_price=1000.0,
            put_quantity=0.01,
        )
        now_ns = time.time_ns()

        # Buyback cost: (1025 * 0.01) + (1000 * 0.01) = 10.25 + 10.0 = 20.25 >= 20.20
        call_q = OptionQuote(bid_price=1020.0, ask_price=1025.0, bid_size=1.0, ask_size=1.0, timestamp_ns=now_ns)
        put_q = OptionQuote(bid_price=995.0, ask_price=1000.0, bid_size=1.0, ask_size=1.0, timestamp_ns=now_ns)

        eval_res = self.rm.evaluate(state, call_q, put_q, current_time_ns=now_ns)
        assert eval_res.action == RiskAction.STOP_LOSS_TRIGGERED
        assert eval_res.buyback_cost == 20.25
        assert eval_res.pnl_amount == -0.25

    def test_take_profit_trigger(self) -> None:
        # Entry: Call 1000, Put 1000, qty 0.01 -> Entry Premium = 20.0
        # TP Threshold = 20.0 * 0.98 = 19.60
        state = self.rm.create_position_state(
            call_fill_price=1000.0,
            call_quantity=0.01,
            put_fill_price=1000.0,
            put_quantity=0.01,
        )
        now_ns = time.time_ns()

        # Buyback cost: (970 * 0.01) + (980 * 0.01) = 9.70 + 9.80 = 19.50 <= 19.60
        call_q = OptionQuote(bid_price=960.0, ask_price=970.0, bid_size=1.0, ask_size=1.0, timestamp_ns=now_ns)
        put_q = OptionQuote(bid_price=975.0, ask_price=980.0, bid_size=1.0, ask_size=1.0, timestamp_ns=now_ns)

        eval_res = self.rm.evaluate(state, call_q, put_q, current_time_ns=now_ns)
        assert eval_res.action == RiskAction.TAKE_PROFIT_TRIGGERED
        assert eval_res.buyback_cost == 19.50
        assert eval_res.pnl_amount == +0.50

    def test_no_action_within_risk_band(self) -> None:
        state = self.rm.create_position_state(
            call_fill_price=1000.0,
            call_quantity=0.01,
            put_fill_price=1000.0,
            put_quantity=0.01,
        )
        now_ns = time.time_ns()

        # Buyback cost: (1000 * 0.01) + (1005 * 0.01) = 20.05 (between 19.60 and 20.20)
        call_q = OptionQuote(bid_price=995.0, ask_price=1000.0, bid_size=1.0, ask_size=1.0, timestamp_ns=now_ns)
        put_q = OptionQuote(bid_price=1000.0, ask_price=1005.0, bid_size=1.0, ask_size=1.0, timestamp_ns=now_ns)

        eval_res = self.rm.evaluate(state, call_q, put_q, current_time_ns=now_ns)
        assert eval_res.action == RiskAction.NO_ACTION
        assert eval_res.buyback_cost == 20.05

    def test_missing_quote_handling(self) -> None:
        state = self.rm.create_position_state(
            call_fill_price=1000.0,
            call_quantity=0.01,
            put_fill_price=1000.0,
            put_quantity=0.01,
        )
        call_q = OptionQuote(bid_price=995.0, ask_price=1000.0, bid_size=1.0, ask_size=1.0, timestamp_ns=time.time_ns())

        # Missing Put quote
        eval_res = self.rm.evaluate(state, call_q, None)
        assert eval_res.action == RiskAction.DATA_MISSING

        # Missing Call quote
        eval_res = self.rm.evaluate(state, None, call_q)
        assert eval_res.action == RiskAction.DATA_MISSING

    def test_invalid_ask_quote_handling(self) -> None:
        state = self.rm.create_position_state(
            call_fill_price=1000.0,
            call_quantity=0.01,
            put_fill_price=1000.0,
            put_quantity=0.01,
        )
        now_ns = time.time_ns()
        zero_ask_q = OptionQuote(bid_price=100.0, ask_price=0.0, bid_size=1.0, ask_size=0.0, timestamp_ns=now_ns)
        valid_q = OptionQuote(bid_price=995.0, ask_price=1000.0, bid_size=1.0, ask_size=1.0, timestamp_ns=now_ns)

        eval_res = self.rm.evaluate(state, zero_ask_q, valid_q, now_ns)
        assert eval_res.action == RiskAction.DATA_MISSING

    def test_stale_quote_handling(self) -> None:
        state = self.rm.create_position_state(
            call_fill_price=1000.0,
            call_quantity=0.01,
            put_fill_price=1000.0,
            put_quantity=0.01,
        )
        now_ns = time.time_ns()
        # Quote is 45 seconds old (max allowable is 30s)
        stale_time_ns = now_ns - int(45 * 1e9)
        stale_q = OptionQuote(bid_price=995.0, ask_price=1000.0, bid_size=1.0, ask_size=1.0, timestamp_ns=stale_time_ns)
        fresh_q = OptionQuote(bid_price=995.0, ask_price=1000.0, bid_size=1.0, ask_size=1.0, timestamp_ns=now_ns)

        eval_res = self.rm.evaluate(state, stale_q, fresh_q, current_time_ns=now_ns)
        assert eval_res.action == RiskAction.DATA_STALE
        assert "stale" in eval_res.reason.lower()
