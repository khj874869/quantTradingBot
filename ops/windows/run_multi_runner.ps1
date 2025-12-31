\
param(
  [string]$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path,
  [string]$ConfigPath = "example_multi_run.json",
  [string]$PythonExe = "python"
)

# 프로젝트 루트로 이동 (.env를 자동 로드하기 위함)
Set-Location $ProjectRoot

# 로그 폴더
if (!(Test-Path ".\logs")) { New-Item -ItemType Directory -Path ".\logs" | Out-Null }

$ts = Get-Date -Format "yyyyMMdd_HHmmss"
$log = ".\logs\multi_runner_$ts.log"

Write-Host "Starting multi_runner..."
Write-Host "ProjectRoot: $ProjectRoot"
Write-Host "ConfigPath : $ConfigPath"
Write-Host "Log        : $log"

# stdout/stderr를 로그로 저장
& $PythonExe -m quantbot.multi_runner $ConfigPath *>> $log
