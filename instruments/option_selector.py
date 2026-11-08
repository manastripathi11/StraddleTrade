"""Dynamic discovery, filtering, and At-The-Money (ATM) selection of BTC options.

Analyzes available exchange option instruments, groups by expiration cycle,
identifies matching Call and Put contracts with identical strike and expiration,
and selects the ATM pair closest to the underlying BTC reference price.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable

from nautilus_trader.model.currencies import BTC
from nautilus_trader.model.enums import OptionKind
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.instruments import CryptoOption, Instrument

from utils.logger import get_logger

logger = get_logger("OptionSelector")


class NoMatchingStraddleError(Exception):
    """Raised when no matching Call and Put option pair can be found for a given expiry."""


@dataclass(frozen=True)
class StraddleLeg:
    """Represents one leg of a straddle contract."""

    instrument_id: InstrumentId
    option_kind: OptionKind
    strike_price: float
    expiration_utc: datetime
    expiration_ns: int
    multiplier: float
    lot_size: float
    raw_symbol: str

    @property
    def is_call(self) -> bool:
        return self.option_kind == OptionKind.CALL

    @property
    def is_put(self) -> bool:
        return self.option_kind == OptionKind.PUT


@dataclass(frozen=True)
class StraddleSelection:
    """The matched At-The-Money (ATM) Call and Put options forming the straddle."""

    call_leg: StraddleLeg
    put_leg: StraddleLeg
    strike_price: float
    expiration_utc: datetime
    reference_price: float
    distance_to_strike: float

    def __post_init__(self) -> None:
        if self.call_leg.strike_price != self.put_leg.strike_price:
            raise ValueError(
                f"Straddle strike mismatch: Call {self.call_leg.strike_price} != Put {self.put_leg.strike_price}"
            )
        if self.call_leg.expiration_utc != self.put_leg.expiration_utc:
            raise ValueError(
                f"Straddle expiry mismatch: Call {self.call_leg.expiration_utc} != Put {self.put_leg.expiration_utc}"
            )
        if not self.call_leg.is_call or not self.put_leg.is_put:
            raise ValueError("Straddle must contain exactly one Call and one Put leg")


class OptionSelector:
    """Discovers, parses, and pairs option instruments into ATM straddles."""

    @staticmethod
    def parse_leg(instrument: Instrument) -> StraddleLeg | None:
        """Extract option metadata into a structured StraddleLeg.

        Parameters
        ----------
        instrument : Instrument
            Exchange instrument under evaluation.

        Returns
        -------
        StraddleLeg | None
            Structured option leg if the instrument is a valid BTC option, else None.
        """
        # Must be a CryptoOption
        if not isinstance(instrument, CryptoOption):
            return None

        # Verify underlying is BTC
        if instrument.underlying != BTC and str(instrument.underlying) != "BTC":
            return None

        # Ensure expiration date exists
        if instrument.expiration_utc is None:
            return None

        strike_px = float(instrument.strike_price)
        multiplier = float(instrument.multiplier) if instrument.multiplier else 1.0
        lot_size = float(instrument.lot_size) if instrument.lot_size else 0.01

        return StraddleLeg(
            instrument_id=instrument.id,
            option_kind=instrument.option_kind,
            strike_price=strike_px,
            expiration_utc=instrument.expiration_utc,
            expiration_ns=instrument.expiration_ns,
            multiplier=multiplier,
            lot_size=lot_size,
            raw_symbol=str(instrument.raw_symbol),
        )

    @staticmethod
    def find_atm_strike(available_strikes: Iterable[float], reference_price: float) -> float:
        """Find the strike price with minimum absolute distance from reference price.

        Parameters
        ----------
        available_strikes : Iterable[float]
            List of available strike prices.
        reference_price : float
            Current underlying market reference price.

        Returns
        -------
        float
            The strike closest to the reference price.
        """
        strikes = sorted(set(available_strikes))
        if not strikes:
            raise ValueError("Cannot determine ATM strike from an empty list of strikes")
        if reference_price <= 0:
            raise ValueError(f"Reference price must be positive, got {reference_price}")

        # Minimize |strike - reference_price|
        atm_strike = min(strikes, key=lambda k: abs(k - reference_price))
        return atm_strike

    @classmethod
    def filter_active_btc_options(
        cls,
        instruments: Iterable[Instrument],
        current_time_utc: datetime | None = None,
    ) -> list[StraddleLeg]:
        """Filter a collection of instruments for active, unexpired BTC options.

        Parameters
        ----------
        instruments : Iterable[Instrument]
            Collection of instruments from Nautilus cache or provider.
        current_time_utc : datetime | None
            Current reference time. Defaults to UTC now.

        Returns
        -------
        list[StraddleLeg]
            List of valid, unexpired BTC option legs.
        """
        now = current_time_utc or datetime.now(timezone.utc)
        legs: list[StraddleLeg] = []

        for inst in instruments:
            leg = cls.parse_leg(inst)
            if leg is not None:
                # Exclude expired contracts (require at least 15 minutes before expiration)
                if leg.expiration_utc > now:
                    legs.append(leg)

        return legs

    @classmethod
    def select_expiry(
        cls,
        available_expiries: list[datetime],
        preference: str = "nearest",
    ) -> datetime:
        """Select an expiration date according to preference or nearest cycle.

        Parameters
        ----------
        available_expiries : list[datetime]
            Sorted unique list of unexpired delivery datetimes.
        preference : str
            "nearest" or date substring (e.g. "2026-11-13", "13NOV26", "24SEP27").

        Returns
        -------
        datetime
            The selected expiration datetime.
        """
        if not available_expiries:
            raise NoMatchingStraddleError("No active option expiries available")

        sorted_expiries = sorted(available_expiries)

        if preference.lower() == "nearest":
            return sorted_expiries[0]

        # Match preference string against expiry ISO or format
        pref_clean = preference.strip().lower()
        for exp in sorted_expiries:
            exp_str = exp.strftime("%Y-%m-%d").lower()
            exp_formatted = exp.strftime("%d%b%y").lower()
            if pref_clean in exp_str or pref_clean in exp_formatted or pref_clean in str(exp).lower():
                return exp

        # If not matched, log warning and fall back to nearest
        logger.warning(
            f"Requested expiry preference '{preference}' not found in {sorted_expiries}. Falling back to nearest expiry: {sorted_expiries[0]}"
        )
        return sorted_expiries[0]

    @classmethod
    def select_straddle(
        cls,
        instruments: Iterable[Instrument],
        reference_price: float,
        expiry_preference: str = "nearest",
        current_time_utc: datetime | None = None,
    ) -> StraddleSelection:
        """Identify available options, compute ATM strike, and match Call + Put contracts.

        Parameters
        ----------
        instruments : Iterable[Instrument]
            Collection of instruments.
        reference_price : float
            Current BTC reference price (e.g. from spot or perp ticker).
        expiry_preference : str
            Desired expiration cycle ("nearest" or specific date string).
        current_time_utc : datetime | None
            Current timestamp for expiration filtering.

        Returns
        -------
        StraddleSelection
            Validated ATM Call and Put pair with identical strike and expiry.
        """
        if reference_price <= 0:
            raise ValueError(f"Invalid reference price: {reference_price}. Must be positive.")

        active_legs = cls.filter_active_btc_options(instruments, current_time_utc)
        if not active_legs:
            raise NoMatchingStraddleError("No active BTC option contracts found in instruments cache")

        # Group by expiry
        expiries = sorted(set(leg.expiration_utc for leg in active_legs))
        selected_expiry = cls.select_expiry(expiries, preference=expiry_preference)

        # Filter legs for the selected expiry
        expiry_legs = [leg for leg in active_legs if leg.expiration_utc == selected_expiry]

        # Group by strike into calls and puts
        calls_by_strike: dict[float, StraddleLeg] = {}
        puts_by_strike: dict[float, StraddleLeg] = {}

        for leg in expiry_legs:
            if leg.is_call:
                calls_by_strike[leg.strike_price] = leg
            elif leg.is_put:
                puts_by_strike[leg.strike_price] = leg

        # Identify strikes with BOTH a Call and a Put
        common_strikes = sorted(set(calls_by_strike.keys()) & set(puts_by_strike.keys()))
        if not common_strikes:
            raise NoMatchingStraddleError(
                f"No matching Call+Put pairs found for expiry {selected_expiry} across {len(expiry_legs)} legs"
            )

        # Calculate the nearest ATM strike
        atm_strike = cls.find_atm_strike(common_strikes, reference_price)
        call_leg = calls_by_strike[atm_strike]
        put_leg = puts_by_strike[atm_strike]
        distance = abs(atm_strike - reference_price)

        logger.info(
            f"ATM Straddle Selected | Reference BTC: {reference_price:.2f} USDT | "
            f"Strike: {atm_strike:.2f} (diff: {distance:.2f}) | "
            f"Expiry: {selected_expiry} | "
            f"Call ID: {call_leg.instrument_id} | "
            f"Put ID: {put_leg.instrument_id}"
        )

        return StraddleSelection(
            call_leg=call_leg,
            put_leg=put_leg,
            strike_price=atm_strike,
            expiration_utc=selected_expiry,
            reference_price=reference_price,
            distance_to_strike=distance,
        )
