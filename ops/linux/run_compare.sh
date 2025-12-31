#!/usr/bin/env bash
set -euo pipefail

DAYS="${1:-2}"

python -m quantbot.reporting.compare_report --state-dir state --days "$DAYS" \
  --exp v0,binance_demo_v0,binance_futures,BTCUSDT \
  --exp v1,binance_demo_v1,binance_futures,BTCUSDT \
  --exp v2,binance_demo_v2,binance_futures,BTCUSDT
