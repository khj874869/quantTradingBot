@echo off
setlocal

set "SYMBOL=%~1"
if "%SYMBOL%"=="" set "SYMBOL=BTCUSDT"
set "VENUE=binance_futures"

set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1

start "Quantbot Monitor" powershell -ExecutionPolicy Bypass -File ops\windows\scalp_state_watch.ps1 -Symbol %SYMBOL% -Venue %VENUE% -IntervalSec 5

py -V:3.14 -m quantbot.main --mode live --venue %VENUE% --strategy scalp --symbols %SYMBOL% ^
  --preset binance_futures_btc_scalp_relaxed --leverage 10

endlocal
