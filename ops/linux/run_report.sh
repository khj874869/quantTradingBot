#!/usr/bin/env bash
set -euo pipefail

STATE_DIR="${1:-state}"
DAYS="${2:-30}"

python -m quantbot.reporting.auto_report --state-dir "$STATE_DIR" --days "$DAYS"
