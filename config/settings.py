"""Application settings and environment validation for BTC Short Straddle trading system.

Validates environment variables from `.env`, enforces safety boundaries (defaulting
to Bybit Testnet), and ensures correct parameter configuration across strategy levels.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from pydantic import BaseModel, Field, field_validator, model_validator


class AppSettings(BaseModel):
    """Central configuration model for the trading system."""

    # Exchange Credentials
    bybit_api_key: str = Field(default="", description="Bybit Testnet API Key")
    bybit_api_secret: str = Field(default="", description="Bybit Testnet API Secret")

    # Environment
    bybit_testnet: bool = Field(default=True, description="Enforce testnet execution")
    trading_environment: Literal["testnet", "mainnet"] = Field(
        default="testnet",
        description="Target trading environment",
    )

    # Strategy Execution Controls
    strategy_enabled: bool = Field(
        default=False,
        description="Safety master switch. False = Dry Run / Monitor only; True = Live Order Submission",
    )
    strategy_level: Literal[1, 2] = Field(
        default=1,
        description="1 = Naked Short Straddle; 2 = Automated SL/TP Risk Management",
    )
    underlying_symbol: str = Field(
        default="BTCUSDT-SPOT.BYBIT",
        description="Underlying spot/linear symbol used for reference price discovery",
    )
    trade_quantity: float = Field(
        default=0.01,
        gt=0.0,
        description="Option contracts quantity per leg (0.01 BTC minimum on Bybit)",
    )
    expiry_preference: str = Field(
        default="nearest",
        description="'nearest' or specific expiry string (e.g. '2026-11-13' or '24SEP27')",
    )

    # Risk Management (Level 2)
    stop_loss_pct: float = Field(
        default=0.01,
        gt=0.0,
        description="Stop-loss threshold percentage (0.01 = 1% increase in buyback cost)",
    )
    take_profit_pct: float = Field(
        default=0.02,
        gt=0.0,
        description="Take-profit threshold percentage (0.02 = 2% decrease in buyback cost)",
    )
    max_quote_stale_secs: float = Field(
        default=30.0,
        gt=0.0,
        description="Maximum quote age in seconds before quotes are deemed stale",
    )

    # Application & Logging
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = Field(
        default="INFO",
        description="Application logging verbosity",
    )

    @field_validator("trade_quantity")
    @classmethod
    def validate_quantity(cls, val: float) -> float:
        if val <= 0:
            raise ValueError("trade_quantity must be greater than zero")
        return round(val, 4)

    @model_validator(mode="after")
    def validate_safety_and_credentials(self) -> AppSettings:
        # Strict protection against unintended mainnet live execution
        if not self.bybit_testnet or self.trading_environment == "mainnet":
            # For this, trading must remain on Testnet
            raise ValueError(
                "CRITICAL SAFETY VIOLATION: Live mainnet execution is disabled in this build. "
                "Set BYBIT_TESTNET=true and TRADING_ENVIRONMENT=testnet in your .env file."
            )

        # Validate credentials when strategy is enabled for real order submission
        if self.strategy_enabled:
            if not self.bybit_api_key or self.bybit_api_key.startswith("YOUR_"):
                raise ValueError(
                    "Configuration Error: STRATEGY_ENABLED=true requires a valid BYBIT_API_KEY. "
                    "Please set your Bybit Testnet key in .env or set STRATEGY_ENABLED=false for simulation."
                )
            if not self.bybit_api_secret or self.bybit_api_secret.startswith("YOUR_"):
                raise ValueError(
                    "Configuration Error: STRATEGY_ENABLED=true requires a valid BYBIT_API_SECRET. "
                    "Please set your Bybit Testnet secret in .env or set STRATEGY_ENABLED=false for simulation."
                )

        return self


def load_settings(env_file_path: str | Path | None = None) -> AppSettings:
    """Load configuration from .env file and environment variables.

    Parameters
    ----------
    env_file_path : str | Path | None, optional
        Explicit path to the .env file. Defaults to looking in current directory.

    Returns
    -------
    AppSettings
        Validated application settings instance.
    """
    if env_file_path:
        load_dotenv(dotenv_path=env_file_path, override=True)
    else:
        # Search in current working directory and parents
        env_path = Path.cwd() / ".env"
        if env_path.exists():
            load_dotenv(dotenv_path=env_path, override=True)
        else:
            load_dotenv(override=False)

    return AppSettings(
        bybit_api_key=os.getenv("BYBIT_API_KEY", "").strip(),
        bybit_api_secret=os.getenv("BYBIT_API_SECRET", "").strip(),
        bybit_testnet=os.getenv("BYBIT_TESTNET", "true").lower() in ("true", "1", "yes"),
        trading_environment=os.getenv("TRADING_ENVIRONMENT", "testnet").lower(),  # type: ignore[arg-type]
        strategy_enabled=os.getenv("STRATEGY_ENABLED", "false").lower() in ("true", "1", "yes"),
        strategy_level=int(os.getenv("STRATEGY_LEVEL", "1")),  # type: ignore[arg-type]
        underlying_symbol=os.getenv("UNDERLYING_SYMBOL", "BTCUSDT-SPOT.BYBIT").strip(),
        trade_quantity=float(os.getenv("TRADE_QUANTITY", "0.01")),
        expiry_preference=os.getenv("EXPIRY_PREFERENCE", "nearest").strip(),
        stop_loss_pct=float(os.getenv("STOP_LOSS_PCT", "0.01")),
        take_profit_pct=float(os.getenv("TAKE_PROFIT_PCT", "0.02")),
        max_quote_stale_secs=float(os.getenv("MAX_QUOTE_STALE_SECS", "30.0")),
        log_level=os.getenv("LOG_LEVEL", "INFO").upper(),  # type: ignore[arg-type]
    )
