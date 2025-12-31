# Quantbot Auto Report

This tool generates an offline performance report from:

- `state/fills.jsonl` (filled orders / fills; one JSON per line)
- `state/equity_history.jsonl` (equity snapshots; one JSON per line)

It produces `report.md`, `summary.json`, and CSVs in a timestamped `reports/` folder.

## Run

From project root:

```bash
python -m quantbot.reporting.auto_report --state-dir state --days 30
```

Filter by account tag / venue / symbol:

```bash
python -m quantbot.reporting.auto_report --state-dir state --days 7 --account-tag binance_demo --venue binance_futures --symbol BTCUSDT
```

Disable plots (matplotlib optional):

```bash
python -m quantbot.reporting.auto_report --state-dir state --days 30 --no-plots
```

## Output files

- `report.md` (human summary)
- `summary.json` (machine-readable summary)
- `realized_trades.csv` (realized trades derived via conservative long/short ledger)
- `daily_pnl.csv` (daily realized net PnL)
- `equity_series.csv` (equity snapshots)
- `equity.png`, `daily_pnl.png` if matplotlib is installed

## Notes

- The ledger is conservative: it computes realized PnL when trades reduce an open position and supports both long and short.
- Fees are allocated to realized trades proportionally to the closing size within each fill.
- If your fills already include realized_pnl fields, you can extend the script to trust those instead.


## Compare multiple experiments

### Option A) Compare existing report folders

If you already generated per-run reports with `auto_report`, compare their `summary.json` outputs:

```bash
python -m quantbot.reporting.compare_report --report-dirs reports/20250101_120000 reports/20250101_140000
```

### Option B) Compare directly from the current `state/` (recommended if you used distinct account_tag per run)

Example comparing v0/v1/v2 for Binance Futures BTCUSDT:

```bash
python -m quantbot.reporting.compare_report --state-dir state --days 2 \
  --exp v0,binance_demo_v0,binance_futures,BTCUSDT \
  --exp v1,binance_demo_v1,binance_futures,BTCUSDT \
  --exp v2,binance_demo_v2,binance_futures,BTCUSDT
```

This writes `reports/compare_<timestamp>/compare.md` and `compare.csv`.
