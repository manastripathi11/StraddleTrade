# BTC ATM Options Short Straddle — NautilusTrader System

[![Python 3.12](https://img.shields.io/badge/Python-3.12-blue.svg)](https://www.python.org/downloads/)
[![NautilusTrader 1.231.0](https://img.shields.io/badge/NautilusTrader-1.231.0-orange.svg)](https://nautilustrader.io/)
[![Bybit V5 Testnet](https://img.shields.io/badge/Bybit-V5_Testnet-yellow.svg)](https://testnet.bybit.com/)
[![Tests Passing](https://img.shields.io/badge/Tests-33%20Passed-brightgreen.svg)]()
[![Code Style: Ruff](https://img.shields.io/badge/Code%20Style-Ruff-000000.svg)](https://github.com/astral-sh/ruff)

A production-grade, event-driven algorithmic derivatives trading system implementing an **At-The-Money (ATM) Short Straddle on Bitcoin (BTC) options** connected to **Bybit Testnet** using **NautilusTrader**.

---

## Table of Contents

1. [Project Overview](#1-project-overview)
2. [What is a BTC ATM Short Straddle?](#2-what-is-a-btc-atm-short-straddle)
3. [What is NautilusTrader?](#3-what-is-nautilustrader)
4. [What is Bybit Testnet?](#4-what-is-bybit-testnet)
5. [Technology Stack](#5-technology-stack)
6. [Project Architecture](#6-project-architecture)
7. [Folder-by-Folder Structure](#7-folder-by-folder-structure)
8. [Prerequisites & System Requirements](#8-prerequisites--system-requirements)
9. [Installation & Setup](#9-installation--setup)
10. [Bybit Testnet Account & API Credentials](#10-bybit-testnet-account--api-credentials)
11. [Configuration (`.env`)](#11-configuration-env)
12. [Running the Strategy](#12-running-the-strategy)
    - [Running Level 1: Naked Short Straddle](#running-level-1-naked-short-straddle)
    - [Running Level 2: Automated Risk Management](#running-level-2-automated-risk-management)
13. [Running Tests](#13-running-tests)
14. [Expected Terminal Output](#14-expected-terminal-output)
15. [Common Errors & Troubleshooting](#15-common-errors--troubleshooting)
16. [Security & Risk Precautions](#16-security--risk-precautions)
17. [Production Deployment](#17-production-deployment)
18. [Known Limitations](#18-known-limitations)

---

## 1. Project Overview

This repository provides a production-grade algorithmic trading system for executing an **At-The-Money (ATM) Short Straddle on Bitcoin (BTC) options** across two operational execution modes:

- **Level 1 — Naked Short Straddle**: Dynamic underlying BTC price discovery, exchange option instrument querying, ATM strike selection, matching Call and Put contracts with identical strike and expiration, order submission, fill tracking, and position monitoring.
- **Level 2 — Automated Risk Management**: Real-time combined buyback cost valuation using live Ask quotes, dynamic entry premium tracking, automated **+1% Stop-Loss** exit execution, automated **-2% Take-Profit** exit execution, leg imbalance detection, and atomic duplicate-exit protection.

---

## 2. What is a BTC ATM Short Straddle?

A **Short Straddle** is a market-neutral options trading strategy where a trader simultaneously sells:
1. **One At-The-Money (ATM) Call Option**
2. **One At-The-Money (ATM) Put Option**

Both contracts share the **exact same strike price ($K$)** and **expiration date ($T$)**.

### Trading Thesis
- **Premium Collection**: The seller collects an initial combined premium:
  $$\text{Entry Premium} = (\text{Call Fill Price} \times Q_{\text{call}}) + (\text{Put Fill Price} \times Q_{\text{put}})$$
- **Volatility Decay (Short Vega & Theta)**: The strategy is profitable when BTC remains range-bound around the strike price until expiration, or when implied volatility contracts faster than underlying price movement.
- **Risk Profile**: If BTC moves violently up or down, the buyback cost to close the short contracts increases, creating unlimited theoretical risk unless controlled by an automated stop-loss.

---

## 3. What is NautilusTrader?

[NautilusTrader](https://nautilustrader.io/) is an institutional-grade, event-driven algorithmic trading engine written in **Rust** and **Python (PyO3/Cython)**. It provides:
- Sub-millisecond latency event loops.
- Asynchronous execution and market data gateways.
- Native exchange adapters (including Bybit V5).
- In-memory cache, message bus, and portfolio accounting.

---

## 4. What is Bybit Testnet?

[Bybit Testnet](https://testnet.bybit.com/) is an official sandbox environment provided by Bybit for algorithmic and manual trade testing. It mirrors Bybit’s production Unified Trading Account (UTA) architecture, matching engine, and V5 REST/WebSocket endpoints without risking capital.

---

## 5. Technology Stack

- **Python**: 3.12+ (CPython)
- **Engine**: NautilusTrader 1.231.0
- **Exchange Adapter**: `nautilus-bybit` (Bybit V5 REST & WebSocket)
- **Validation**: Pydantic 2.10+
- **Configuration**: `python-dotenv`
- **Testing**: Pytest 9.2+ (33 automated unit & integration tests)
- **Linting & Quality**: Ruff 0.8+
- **Containerization**: Docker & Docker Compose

---

## 6. Project Architecture

```
btc-short-straddle/
│
├── config/                  # Environment loading & Pydantic validation
│   ├── __init__.py
│   └── settings.py
│
├── instruments/             # Dynamic option chain discovery & ATM strike selector
│   ├── __init__.py
│   └── option_selector.py
│
├── strategies/              # Nautilus Strategy & Level 2 Risk Manager
│   ├── __init__.py
│   ├── short_straddle.py    # Main event-driven strategy
│   └── risk_manager.py      # Premium tracking, SL (+1%), TP (-2%)
│
├── execution/               # Order lifecycle, fills, & duplicate exit locking
│   ├── __init__.py
│   └── order_manager.py
│
├── monitoring/              # Real-time position monitor & startup reconciliation
│   ├── __init__.py
│   └── position_monitor.py
│
├── utils/                   # Structured logging & credential redaction
│   ├── __init__.py
│   └── logger.py
│
├── tests/                   # Automated test suite (33 tests)
│   ├── helpers.py           # Nautilus Cython models & event mocks
│   ├── test_option_selector.py
│   ├── test_risk_manager.py
│   ├── test_order_manager.py
│   └── test_strategy.py
│
├── main.py                  # Trading Node initialization & CLI entry point
├── requirements.txt         # Core dependencies
├── pyproject.toml           # Ruff & Pytest configuration
├── pytest.ini               # Pytest runtime configuration
├── Dockerfile               # Production Docker image
├── docker-compose.yml       # Production Docker service configuration
├── .dockerignore
├── .gitignore
├── .env.example             # Safe template
└── README.md                # Comprehensive system architecture & operation guide
```

---

## 7. Folder-by-Folder Structure

| Directory | Purpose | Core Classes & Modules |
|---|---|---|
| `config/` | Validates environment variables, enforces Testnet-only safeguards, and parses runtime parameters. | `AppSettings`, `load_settings()` |
| `instruments/` | Parses option metadata, groups expiries, filters unexpired contracts, and computes ATM strike $\min_K \|K - P_{\text{BTC}}\|$. | `OptionSelector`, `StraddleLeg`, `StraddleSelection` |
| `strategies/` | Implements the core Nautilus `Strategy` lifecycle (`on_quote_tick`, `on_order_filled`, etc.) and Level 2 mathematical risk bounds. | `ShortStraddleStrategy`, `ShortStraddleConfig`, `RiskManager`, `StraddlePositionState` |
| `execution/` | Tracks order state machine, handles partial fills, checks leg imbalances, and enforces atomic exit locking. | `OrderManager`, `LegOrderTracker`, `OrderRole`, `LegType` |
| `monitoring/` | Reconciles positions from cache on startup and verifies confirmed execution events before marking positions closed. | `PositionMonitor` |
| `utils/` | Colorized structured logging with automatic regex filtering of credentials and secrets. | `setup_logging()`, `get_logger()`, `SensitiveDataFilter` |
| `tests/` | Exhaustive automated test coverage for ATM selection, risk formulas, duplicate prevention, and strategy events. | 33 automated tests in `tests/` |

---

## 8. Prerequisites & System Requirements

- **Operating System**: Linux (Ubuntu 22.04 / 24.04 LTS recommended), macOS, or Windows (WSL2).
- **Python**: Version 3.12 or higher.
- **Build Tools**: `curl`, `ca-certificates`, `git`.

---

## 9. Installation & Setup

### Step 1: Clone Repository
```bash
git clone https://github.com/your-username/StraddleTrade.git
cd StraddleTrade
```

### Step 2: Set Up Virtual Environment (Python 3.12)
```bash
python3.12 -m venv .venv
source .venv/bin/activate
```

### Step 3: Install Dependencies
```bash
pip install --upgrade pip
pip install -r requirements.txt
```

---

## 10. Bybit Testnet Account & API Credentials

1. Go to [https://testnet.bybit.com/](https://testnet.bybit.com/) and register or log in.
2. Ensure your account is set to **Unified Trading Account (UTA)** mode.
3. Claim test funds from the Testnet Faucet (USDT/USDC).
4. Navigate to **API Management**: [https://testnet.bybit.com/app/user/api-management](https://testnet.bybit.com/app/user/api-management).
5. Click **Create New Key** -> **System-generated API Keys**.
6. Select **Read-Write** permissions.
7. Under permissions, select **Orders** and **Positions** (under Unified Trading Account).
8. Copy your **API Key** and **API Secret**.

---

## 11. Configuration (`.env`)

Copy `.env.example` to create your local `.env` file:
```bash
cp .env.example .env
```

Edit `.env` with your editor:
```ini
# Bybit Testnet API Credentials
BYBIT_API_KEY=your_actual_testnet_api_key
BYBIT_API_SECRET=your_actual_testnet_api_secret

# Enforce Testnet
BYBIT_TESTNET=true
TRADING_ENVIRONMENT=testnet

# Safety Toggle: Set to true only when you want actual orders submitted to Bybit Testnet
STRATEGY_ENABLED=false

# Strategy Level: 1 = Naked Straddle, 2 = Automated SL/TP
STRATEGY_LEVEL=1

# Strategy Parameters
UNDERLYING_SYMBOL=BTCUSDT-SPOT.BYBIT
TRADE_QUANTITY=0.01
EXPIRY_PREFERENCE=nearest

# Level 2 Risk Thresholds
STOP_LOSS_PCT=0.01
TAKE_PROFIT_PCT=0.02
MAX_QUOTE_STALE_SECS=30.0

LOG_LEVEL=INFO
```

---

## 12. Running the Strategy

### Dry-Run / Simulation Mode (Recommended First Step)
Leave `STRATEGY_ENABLED=false` in `.env` (or pass no trading flag). The strategy will connect to live Bybit Testnet market data, discover real BTC options, pick the nearest ATM strike, and simulate fills and monitoring safely:
```bash
python main.py --level 1
```

### Running Level 1: Naked Short Straddle
To run Level 1 with live order submission on Bybit Testnet:
```bash
python main.py --level 1 --enable-trading
```

### Running Level 2: Automated Risk Management
To run Level 2 (monitoring live quotes and executing +1% Stop-Loss / -2% Take-Profit):
```bash
python main.py --level 2 --enable-trading
```

---

## 13. Running Tests

Run the full suite of 33 automated tests:
```bash
pytest
```

Run tests with verbose per-test execution and output:
```bash
pytest -v
```

Run code formatting and linting check:
```bash
ruff check .
```

---

## 14. Expected Terminal Output

```
2026-11-07 16:30:00 [ INFO] SystemInit: =================================================================
2026-11-07 16:30:00 [ INFO] SystemInit:  NAUTILUS TRADER - BTC ATM SHORT STRADDLE SYSTEM
2026-11-07 16:30:00 [ INFO] SystemInit: =================================================================
2026-11-07 16:30:00 [ INFO] SystemInit:  Environment:        TESTNET (BYBIT TESTNET)
2026-11-07 16:30:00 [ INFO] SystemInit:  Strategy Level:     Level 2 (Automated SL/TP Risk Management)
2026-11-07 16:30:00 [ INFO] SystemInit:  Execution Mode:     LIVE TRADING (Orders will be submitted)
2026-11-07 16:30:00 [ INFO] SystemInit:  Underlying Asset:   BTCUSDT-SPOT.BYBIT
2026-11-07 16:30:00 [ INFO] SystemInit:  Trade Quantity:     0.01 BTC per leg
2026-11-07 16:30:00 [ INFO] SystemInit:  Expiry Preference:  nearest
2026-11-07 16:30:00 [ INFO] SystemInit:  Stop-Loss:          +1.0% increase in buyback cost
2026-11-07 16:30:00 [ INFO] SystemInit:  Take-Profit:        -2.0% decrease in buyback cost
2026-11-07 16:30:00 [ INFO] SystemInit: =================================================================
2026-11-07 16:30:02 [ INFO] ShortStraddleStrategy: Subscribed to underlying quote ticks for BTCUSDT-SPOT.BYBIT
2026-11-07 16:30:05 [ INFO] ShortStraddleStrategy: Underlying Reference Price Received: 76042.40 USDT
2026-11-07 16:30:05 [ INFO] OptionSelector: ATM Straddle Selected | Reference BTC: 76042.40 USDT | Strike: 76000.00 (diff: 42.40) | Expiry: 2026-11-13 08:00:00+00:00 | Call ID: BTC-13NOV26-76000-C-USDT-OPTION.BYBIT | Put ID: BTC-13NOV26-76000-P-USDT-OPTION.BYBIT
2026-11-07 16:30:05 [ INFO] ShortStraddleStrategy: Subscribed to Option Market Data: Call=BTC-13NOV26-76000-C-USDT-OPTION.BYBIT | Put=BTC-13NOV26-76000-P-USDT-OPTION.BYBIT
2026-11-07 16:30:05 [ INFO] ShortStraddleStrategy: Submitted SELL CALL Order: ClientOrderID=ORD-CALL-01 | Qty=0.01
2026-11-07 16:30:05 [ INFO] ShortStraddleStrategy: Submitted SELL PUT Order: ClientOrderID=ORD-PUT-01 | Qty=0.01
2026-11-07 16:30:06 [ INFO] OrderManager: Order FILL: ORD-CALL-01 | Fill Qty=0.01 @ 1250.00 | Total Filled=0.01/0.01
2026-11-07 16:30:06 [ INFO] OrderManager: Order FILL: ORD-PUT-01 | Fill Qty=0.01 @ 1150.00 | Total Filled=0.01/0.01
2026-11-07 16:30:06 [ INFO] RiskManager: Straddle Position Established | Combined Entry Premium: 24.0000 USDT | Stop-Loss Threshold (+1.0%): 24.2400 USDT | Take-Profit Threshold (-2.0%): 23.5200 USDT
2026-11-07 16:30:15 [ INFO] RiskManager: Monitoring Straddle: Buyback Cost=23.8500 USDT, PnL=+0.1500 USDT (+0.62%) within [TP: 23.5200, SL: 24.2400]
2026-11-07 16:30:45 [ WARN] RiskManager: STOP-LOSS TRIGGERED! Buyback Cost (24.3000 USDT) >= Threshold (24.2400 USDT) | Cost increased by 1.25% (Limit: +1.0%) | PnL: -0.3000 USDT (-1.25%)
2026-11-07 16:30:45 [ INFO] OrderManager: Acquired atomic EXIT LOCK. Closing orders permitted.
2026-11-07 16:30:45 [ INFO] ShortStraddleStrategy: Submitted BUY EXIT CALL Order: ClientOrderID=EXIT-CALL-01
2026-11-07 16:30:45 [ INFO] ShortStraddleStrategy: Submitted BUY EXIT PUT Order: ClientOrderID=EXIT-PUT-01
2026-11-07 16:30:46 [ INFO] OrderManager: Straddle EXIT COMPLETED. All positions closed.
2026-11-07 16:30:46 [ INFO] ShortStraddleStrategy: ALL STRADDLE POSITIONS SUCCESSFULLY CLOSED AND CONFIRMED.
```

---

## 15. Common Errors & Troubleshooting

| Error | Root Cause | Solution |
|---|---|---|
| `CRITICAL SAFETY VIOLATION: Live mainnet execution is disabled...` | `.env` has `BYBIT_TESTNET=false` or `TRADING_ENVIRONMENT=mainnet`. | Ensure `BYBIT_TESTNET=true` and `TRADING_ENVIRONMENT=testnet`. |
| `Configuration Error: STRATEGY_ENABLED=true requires a valid BYBIT_API_KEY` | Real trading enabled without setting API keys. | Paste your Bybit Testnet key & secret into `.env`. |
| `No matching Call+Put pairs found for expiry...` | Selected expiry only has Call or Put contracts available. | Specify another expiry via `--expiry` or use `"nearest"`. |
| `Quotes are stale... Cannot evaluate buyback cost` | WebSocket feed disconnected or exchange halted quotes. | RiskManager pauses evaluation and alerts until fresh quotes arrive. |
| `Duplicate exit attempt suppressed by OrderManager lock` | Multiple quote ticks arrived after SL/TP condition met. | Normal behavior: OrderManager's atomic exit lock prevents duplicate exit orders. |

---

## 16. Security & Risk Precautions

1. **Credentials Isolation**: Never commit `.env` to Git. The `.gitignore` excludes all `.env` files.
2. **Log Redaction**: `SensitiveDataFilter` masks all API keys and secrets matching regex patterns.
3. **Fail-Safe Defaults**: `STRATEGY_ENABLED` defaults to `False` (Dry Run). Real orders cannot be submitted unless explicitly enabled.
4. **Testnet Boundary**: The system strictly forbids running against Bybit Mainnet.

---

## 17. Production Deployment

### Option A: Docker Deployment (Recommended)

1. Build the container image:
   ```bash
   docker build -t btc-short-straddle:latest .
   ```
2. Start the container with Docker Compose:
   ```bash
   docker compose up -d
   ```
3. Inspect live streaming logs:
   ```bash
   docker compose logs -f straddle-trader
   ```
4. Graceful shutdown:
   ```bash
   docker compose down
   ```

### Option B: Linux VPS with `systemd`

1. Provision an Ubuntu 22.04/24.04 VPS (e.g. AWS EC2, DigitalOcean Droplet, Hetzner Cloud).
2. Create application user and clone repository into `/opt/straddle-trade`.
3. Set up Python 3.12 virtual environment and `.env`.
4. Create systemd service unit `/etc/systemd/system/straddle-trader.service`:
   ```ini
   [Unit]
   Description=BTC Short Straddle Trading System
   After=network.target

   [Service]
   Type=simple
   User=trader
   WorkingDirectory=/opt/straddle-trade
   EnvironmentFile=/opt/straddle-trade/.env
   ExecStart=/opt/straddle-trade/.venv/bin/python main.py --level 2
   Restart=on-failure
   RestartSec=10s
   TimeoutStopSec=30s
   KillSignal=SIGINT

   [Install]
   WantedBy=multi-user.target
   ```
5. Enable and start service:
   ```bash
   sudo systemctl daemon-reload
   sudo systemctl enable straddle-trader
   sudo systemctl start straddle-trader
   ```
6. Monitor service logs:
   ```bash
   journalctl -u straddle-trader -f
   ```

---

## 18. Known Limitations

- **Bybit Testnet Liquidity**: On Bybit Testnet, options market depth can be sparse, leading to wider bid-ask spreads than on Mainnet.
- **Unified Trading Account (UTA)**: Margin mode must be UTA; standard non-UTA Bybit accounts cannot trade Bybit options.
- **Contract Multiplier**: On Bybit, 1 option contract = 1 BTC. The minimum order quantity is 0.01 BTC.
