param(
  [int]$Days = 2
)

python -m quantbot.reporting.compare_report --state-dir state --days $Days `
  --exp v0,binance_demo_v0,binance_futures,BTCUSDT `
  --exp v1,binance_demo_v1,binance_futures,BTCUSDT `
  --exp v2,binance_demo_v2,binance_futures,BTCUSDT
