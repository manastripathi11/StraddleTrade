# ==============================================================================
# Production Dockerfile for NautilusTrader BTC Options Short Straddle
# Multi-stage / optimized slim build with non-root security execution
# ==============================================================================
FROM python:3.12-slim-bookworm AS base

# Install system dependencies & security certificates
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    tzdata \
    && rm -rf /var/lib/apt/lists/*

# Create dedicated non-root application user
RUN groupadd -g 1000 trader && \
    useradd -u 1000 -g trader -m -s /bin/bash trader

WORKDIR /app

# Ensure Python output is sent directly to terminal without buffering
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=UTC

# Install Python requirements
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copy source code and configuration templates
COPY --chown=trader:trader config/ config/
COPY --chown=trader:trader instruments/ instruments/
COPY --chown=trader:trader strategies/ strategies/
COPY --chown=trader:trader execution/ execution/
COPY --chown=trader:trader monitoring/ monitoring/
COPY --chown=trader:trader utils/ utils/
COPY --chown=trader:trader main.py .
COPY --chown=trader:trader .env.example .

# Switch to non-root trader user
USER trader

# Default command: Runs the trading system with settings from .env or environment
ENTRYPOINT ["python", "main.py"]
CMD ["--level", "1"]
