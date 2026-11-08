"""Main application entry point for the BTC Short Straddle trading system.

Initializes configuration, configures NautilusTrader Live Trading Node with Bybit Testnet
data and execution clients, registers the ShortStraddleStrategy, and manages graceful shutdown.
"""

from __future__ import annotations

import argparse
import signal
import sys
from types import FrameType

from nautilus_trader.adapters.bybit import (
    BybitDataClientConfig,
    BybitEnvironment,
    BybitExecClientConfig,
    BybitProductType,
)
from nautilus_trader.adapters.bybit.factories import (
    BybitLiveDataClientFactory,
    BybitLiveExecClientFactory,
)
from nautilus_trader.config import InstrumentProviderConfig
from nautilus_trader.live.node import TradingNode, TradingNodeConfig

from config.settings import AppSettings, load_settings
from strategies.short_straddle import ShortStraddleConfig, ShortStraddleStrategy
from utils.logger import get_logger, setup_logging


def parse_args() -> argparse.Namespace:
    """Parse command line flags to customize strategy execution parameters."""
    parser = argparse.ArgumentParser(
        description="NautilusTrader BTC Options Short Straddle Trading System (Bybit Testnet)",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--level",
        type=int,
        choices=[1, 2],
        default=None,
        help="Strategy Level: 1 = Naked Short Straddle, 2 = Automated SL/TP Risk Management",
    )
    parser.add_argument(
        "--enable-trading",
        action="store_true",
        default=None,
        help="Enable live order submission on Bybit Testnet (overrides STRATEGY_ENABLED in .env)",
    )
    parser.add_argument(
        "--quantity",
        type=float,
        default=None,
        help="Quantity in BTC per option leg (minimum 0.01 BTC)",
    )
    parser.add_argument(
        "--expiry",
        type=str,
        default=None,
        help="Target expiry preference: 'nearest' or specific date string (e.g. '2026-11-13')",
    )
    parser.add_argument(
        "--env-file",
        type=str,
        default=None,
        help="Path to custom .env configuration file",
    )
    return parser.parse_args()


def build_trading_node(settings: AppSettings, strategy_config: ShortStraddleConfig) -> TradingNode:
    """Build and configure the Nautilus Live Trading Node with Bybit clients and strategy."""
    logger = get_logger("SystemInit")
    logger.info("Configuring Bybit Testnet data and execution adapters...")

    # Data Client Configuration (Subscribes to Spot for reference price and Option markets)
    data_client_config = BybitDataClientConfig(
        environment=BybitEnvironment.TESTNET,
        api_key=settings.bybit_api_key or None,
        api_secret=settings.bybit_api_secret or None,
        product_types=(BybitProductType.SPOT, BybitProductType.OPTION),
        instrument_provider=InstrumentProviderConfig(load_all=True),
    )

    # Execution Client Configuration (Executes Option orders on Unified Trading Account)
    exec_client_config = BybitExecClientConfig(
        environment=BybitEnvironment.TESTNET,
        api_key=settings.bybit_api_key or "DUMMY_KEY_DRY_RUN",
        api_secret=settings.bybit_api_secret or "DUMMY_SECRET_DRY_RUN",
        product_types=(BybitProductType.OPTION,),
        instrument_provider=InstrumentProviderConfig(load_all=True),
    )

    # Live Trading Node Configuration
    node_config = TradingNodeConfig(
        trader_id="STRADDLE-TRADER-01",
        data_clients={"BYBIT": data_client_config},
        exec_clients={"BYBIT": exec_client_config},
    )

    node = TradingNode(config=node_config)

    # Register Bybit Client Factories
    node.add_data_client_factory("BYBIT", BybitLiveDataClientFactory)
    node.add_exec_client_factory("BYBIT", BybitLiveExecClientFactory)

    # Build internal engines
    node.build()
    logger.info("Nautilus TradingNode built successfully.")

    # Instantiate and register ShortStraddleStrategy
    strategy = ShortStraddleStrategy(config=strategy_config)
    node.trader.add_strategy(strategy)
    logger.info(f"Registered ShortStraddleStrategy (ID: {strategy.id}) with Trader.")

    return node


def main() -> None:
    """Application main entry point."""
    args = parse_args()

    # Load configuration
    try:
        settings = load_settings(env_file_path=args.env_file)
    except Exception as exc:
        print(f"\n[FATAL CONFIGURATION ERROR] {exc}\n", file=sys.stderr)
        sys.exit(1)

    # Apply CLI overrides if provided
    active_level = args.level if args.level is not None else settings.strategy_level
    active_enabled = args.enable_trading if args.enable_trading is not None else settings.strategy_enabled
    active_quantity = args.quantity if args.quantity is not None else settings.trade_quantity
    active_expiry = args.expiry if args.expiry is not None else settings.expiry_preference

    # Initialize structured logging
    logger = setup_logging(settings.log_level)

    logger.info("=================================================================")
    logger.info(" NAUTILUS TRADER - BTC ATM SHORT STRADDLE SYSTEM")
    logger.info("=================================================================")
    logger.info(f" Environment:        {settings.trading_environment.upper()} (BYBIT TESTNET)")
    logger.info(f" Strategy Level:     Level {active_level} ({'Automated SL/TP Risk Management' if active_level == 2 else 'Naked Straddle'})")
    logger.info(f" Execution Mode:     {'LIVE TRADING (Orders will be submitted)' if active_enabled else 'DRY RUN / MONITOR ONLY'}")
    logger.info(f" Underlying Asset:   {settings.underlying_symbol}")
    logger.info(f" Trade Quantity:     {active_quantity} BTC per leg")
    logger.info(f" Expiry Preference:  {active_expiry}")
    if active_level == 2:
        logger.info(f" Stop-Loss:          +{settings.stop_loss_pct * 100:.1f}% increase in buyback cost")
        logger.info(f" Take-Profit:        -{settings.take_profit_pct * 100:.1f}% decrease in buyback cost")
    logger.info("=================================================================")

    # Construct Strategy Configuration
    strategy_config = ShortStraddleConfig(
        underlying_symbol=settings.underlying_symbol,
        trade_quantity=active_quantity,
        level=active_level,
        stop_loss_pct=settings.stop_loss_pct,
        take_profit_pct=settings.take_profit_pct,
        expiry_preference=active_expiry,
        strategy_enabled=active_enabled,
        max_quote_stale_secs=settings.max_quote_stale_secs,
    )

    try:
        node = build_trading_node(settings, strategy_config)
    except Exception as exc:
        logger.exception(f"Failed to initialize TradingNode: {exc}")
        sys.exit(1)

    # Signal handling for safe graceful termination
    def handle_shutdown_signal(signum: int, frame: FrameType | None) -> None:
        signame = signal.Signals(signum).name
        logger.warning(f"Received shutdown signal {signame}. Stopping trading node...")
        node.stop()

    signal.signal(signal.SIGINT, handle_shutdown_signal)
    signal.signal(signal.SIGTERM, handle_shutdown_signal)

    # Run Trading Node
    logger.info("Starting live trading engine (Press Ctrl+C to stop)...")
    try:
        node.run()
    except Exception as exc:
        logger.exception(f"Trading system runtime error: {exc}")
        sys.exit(1)
    finally:
        logger.info("Trading system shutdown completed cleanly.")


if __name__ == "__main__":
    main()
