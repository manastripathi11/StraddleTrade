"""Unit tests for ATM strike calculation, option discovery, and straddle pairing."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from nautilus_trader.model.enums import OptionKind
from nautilus_trader.model.identifiers import InstrumentId

from instruments.option_selector import (
    NoMatchingStraddleError,
    OptionSelector,
    StraddleLeg,
    StraddleSelection,
)
from tests.helpers import make_mock_crypto_option


class TestOptionSelector:
    """Tests for OptionSelector ATM calculation and pair matching."""

    def test_find_atm_strike_exact_match(self) -> None:
        strikes = [70000.0, 75000.0, 80000.0]
        atm = OptionSelector.find_atm_strike(strikes, reference_price=75000.0)
        assert atm == 75000.0

    def test_find_atm_strike_closest_above(self) -> None:
        strikes = [70000.0, 75000.0, 80000.0]
        atm = OptionSelector.find_atm_strike(strikes, reference_price=73500.0)
        assert atm == 75000.0

    def test_find_atm_strike_closest_below(self) -> None:
        strikes = [70000.0, 75000.0, 80000.0]
        atm = OptionSelector.find_atm_strike(strikes, reference_price=71500.0)
        assert atm == 70000.0

    def test_find_atm_strike_empty_list_raises(self) -> None:
        with pytest.raises(ValueError, match="empty list"):
            OptionSelector.find_atm_strike([], reference_price=75000.0)

    def test_find_atm_strike_non_positive_price_raises(self) -> None:
        with pytest.raises(ValueError, match="must be positive"):
            OptionSelector.find_atm_strike([70000.0], reference_price=0.0)

    def test_straddle_selection_matching_pair(self) -> None:
        future_exp = datetime.now(timezone.utc) + timedelta(days=5)
        call_opt = make_mock_crypto_option("BTC-TEST-75000-C-USDT", OptionKind.CALL, 75000.0, future_exp)
        put_opt = make_mock_crypto_option("BTC-TEST-75000-P-USDT", OptionKind.PUT, 75000.0, future_exp)

        # Additional strike with only Call (no matching Put)
        lone_call = make_mock_crypto_option("BTC-TEST-80000-C-USDT", OptionKind.CALL, 80000.0, future_exp)

        instruments = [call_opt, put_opt, lone_call]
        selection = OptionSelector.select_straddle(
            instruments=instruments,
            reference_price=76000.0,
            expiry_preference="nearest",
        )

        assert selection.strike_price == 75000.0
        assert selection.call_leg.strike_price == 75000.0
        assert selection.put_leg.strike_price == 75000.0
        assert selection.call_leg.expiration_utc == selection.put_leg.expiration_utc
        assert selection.call_leg.is_call
        assert selection.put_leg.is_put
        assert selection.distance_to_strike == 1000.0

    def test_straddle_selection_ignores_expired_options(self) -> None:
        now = datetime.now(timezone.utc)
        expired_date = now - timedelta(days=1)
        future_date = now + timedelta(days=7)

        expired_call = make_mock_crypto_option("BTC-EXP-75000-C-USDT", OptionKind.CALL, 75000.0, expired_date)
        expired_put = make_mock_crypto_option("BTC-EXP-75000-P-USDT", OptionKind.PUT, 75000.0, expired_date)

        active_call = make_mock_crypto_option("BTC-ACT-75000-C-USDT", OptionKind.CALL, 75000.0, future_date)
        active_put = make_mock_crypto_option("BTC-ACT-75000-P-USDT", OptionKind.PUT, 75000.0, future_date)

        instruments = [expired_call, expired_put, active_call, active_put]
        selection = OptionSelector.select_straddle(instruments, reference_price=75200.0)

        assert abs(selection.expiration_utc.timestamp() - future_date.timestamp()) < 1.0
        assert selection.strike_price == 75000.0

    def test_straddle_selection_no_matching_straddle_raises(self) -> None:
        future_exp = datetime.now(timezone.utc) + timedelta(days=3)
        # Call at 70000, Put at 75000 (no overlapping strike)
        call_opt = make_mock_crypto_option("BTC-TEST-70000-C-USDT", OptionKind.CALL, 70000.0, future_exp)
        put_opt = make_mock_crypto_option("BTC-TEST-75000-P-USDT", OptionKind.PUT, 75000.0, future_exp)

        with pytest.raises(NoMatchingStraddleError, match="No matching Call\\+Put pairs found"):
            OptionSelector.select_straddle([call_opt, put_opt], reference_price=72000.0)

    def test_straddle_selection_invariant_enforcement(self) -> None:
        future_exp = datetime.now(timezone.utc) + timedelta(days=3)
        call_leg = StraddleLeg(
            instrument_id=InstrumentId.from_str("BTC-C-OPTION.BYBIT"),
            option_kind=OptionKind.CALL,
            strike_price=70000.0,
            expiration_utc=future_exp,
            expiration_ns=int(future_exp.timestamp() * 1e9),
            multiplier=1.0,
            lot_size=0.01,
            raw_symbol="BTC-C",
        )
        # Mismatched strike
        put_leg_mismatched = StraddleLeg(
            instrument_id=InstrumentId.from_str("BTC-P-OPTION.BYBIT"),
            option_kind=OptionKind.PUT,
            strike_price=75000.0,
            expiration_utc=future_exp,
            expiration_ns=int(future_exp.timestamp() * 1e9),
            multiplier=1.0,
            lot_size=0.01,
            raw_symbol="BTC-P",
        )

        with pytest.raises(ValueError, match="strike mismatch"):
            StraddleSelection(
                call_leg=call_leg,
                put_leg=put_leg_mismatched,
                strike_price=70000.0,
                expiration_utc=future_exp,
                reference_price=72000.0,
                distance_to_strike=2000.0,
            )
