\
param(
  [string]$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path,
  [int]$Port = 8899,
  [string]$PythonExe = "python"
)

Set-Location $ProjectRoot
if (!(Test-Path ".\logs")) { New-Item -ItemType Directory -Path ".\logs" | Out-Null }

$ts = Get-Date -Format "yyyyMMdd_HHmmss"
$log = ".\logs\dashboard_$ts.log"

Write-Host "Starting dashboard..."
Write-Host "URL: http://127.0.0.1:$Port"
Write-Host "Log: $log"

# 포트 인자를 지원하지 않는 경우가 있어 env로도 전달
$env:DASHBOARD_PORT = "$Port"
& $PythonExe -m quantbot.dashboard.server *>> $log
