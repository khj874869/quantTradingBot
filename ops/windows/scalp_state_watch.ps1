param(
    [string]$Symbol = "BTCUSDT",
    [string]$Venue = "binance_futures",
    [int]$IntervalSec = 5
)

$statePath = Join-Path -Path "state\\bots" -ChildPath ("{0}_{1}.json" -f $Venue, $Symbol)
$envPath = ".env"

Write-Host ("Watching {0} (every {1}s). Ctrl+C to stop." -f $statePath, $IntervalSec)

while ($true) {
    if (Test-Path $statePath) {
        try {
            $raw = Get-Content -Raw -Path $statePath
            $obj = $raw | ConvertFrom-Json
            $ls = $obj.last_signal
            $meta = $ls.meta
            $posQty = $obj.position.qty
            $stopLoss = $null
            $trailing = $null
            if (Test-Path $envPath) {
                $envLines = Get-Content -Path $envPath
                foreach ($ln in $envLines) {
                    if ($ln -match '^STOP_LOSS_PCT=') {
                        $stopLoss = ($ln -split '=', 2)[1]
                    } elseif ($ln -match '^TRAILING_STOP_PCT=') {
                        $trailing = ($ln -split '=', 2)[1]
                    }
                }
            }

            $line = @(
                $obj.ts,
                $ls.side,
                $meta.reason,
                ("tp={0}" -f $meta.tp),
                ("ob_imb={0}" -f $meta.ob_imb),
                ("rsi={0}" -f $meta.rsi),
                ("pos_qty={0}" -f $posQty),
                ("sl={0}" -f $stopLoss),
                ("trail={0}" -f $trailing)
            ) -join " | "
            Write-Host $line

            $events = @($obj.events)
            if ($events.Count -gt 0) {
                $tail = $events | Select-Object -Last 5
                foreach ($ev in $tail) {
                    $evLine = @(
                        "EVENT",
                        $ev.ts,
                        $ev.type,
                        $ev.side,
                        ("qty={0}" -f $ev.qty),
                        ("px={0}" -f $ev.price),
                        ("reason={0}" -f $ev.reason)
                    ) -join " | "
                    Write-Host $evLine
                }
            } else {
                Write-Host "EVENT | (none)"
            }
        } catch {
            Write-Host ("Failed to read/parse state: {0}" -f $_.Exception.Message)
        }
    } else {
        Write-Host ("State file not found: {0}" -f $statePath)
    }
    Start-Sleep -Seconds $IntervalSec
}
