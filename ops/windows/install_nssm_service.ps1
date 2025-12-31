\
<#
NSSM으로 QuantBot를 Windows Service로 등록합니다(24/7 운영에 가장 추천).

준비물:
- nssm.exe (https://nssm.cc) 를 내려받아 이 폴더에 두거나 PATH에 추가

사용(관리자 PowerShell):
  cd ops\windows
  .\install_nssm_service.ps1 -ProjectRoot "C:\path\to\quantbot_complete_final" -PythonExe "C:\Python311\python.exe"

서비스는 2개를 등록합니다:
- QuantBot-MultiRunner
- QuantBot-Dashboard
#>

param(
  [Parameter(Mandatory=$true)][string]$ProjectRoot,
  [string]$PythonExe = "python",
  [string]$MultiConfig = "example_multi_run.json",
  [int]$DashboardPort = 8899,
  [string]$NssmExe = "$PSScriptRoot\nssm.exe"
)

if (!(Test-Path $NssmExe)) {
  Write-Host "❌ nssm.exe 를 찾을 수 없습니다: $NssmExe"
  Write-Host "   nssm.exe를 ops\windows 폴더에 두거나 -NssmExe 경로를 지정하세요."
  exit 1
}

$multiArgs = "-m quantbot.multi_runner $MultiConfig"
$dashArgs  = "-m quantbot.dashboard.server"

# MultiRunner service
& $NssmExe install "QuantBot-MultiRunner" $PythonExe $multiArgs
& $NssmExe set "QuantBot-MultiRunner" AppDirectory $ProjectRoot
& $NssmExe set "QuantBot-MultiRunner" AppStdout "$ProjectRoot\logs\nssm_multirunner_out.log"
& $NssmExe set "QuantBot-MultiRunner" AppStderr "$ProjectRoot\logs\nssm_multirunner_err.log"
& $NssmExe set "QuantBot-MultiRunner" AppRotateFiles 1
& $NssmExe set "QuantBot-MultiRunner" AppRotateOnline 1
& $NssmExe set "QuantBot-MultiRunner" AppRotateSeconds 86400
& $NssmExe set "QuantBot-MultiRunner" AppRestartDelay 3000

# Dashboard service
& $NssmExe install "QuantBot-Dashboard" $PythonExe $dashArgs
& $NssmExe set "QuantBot-Dashboard" AppDirectory $ProjectRoot
& $NssmExe set "QuantBot-Dashboard" AppStdout "$ProjectRoot\logs\nssm_dashboard_out.log"
& $NssmExe set "QuantBot-Dashboard" AppStderr "$ProjectRoot\logs\nssm_dashboard_err.log"
& $NssmExe set "QuantBot-Dashboard" AppRotateFiles 1
& $NssmExe set "QuantBot-Dashboard" AppRotateOnline 1
& $NssmExe set "QuantBot-Dashboard" AppRotateSeconds 86400
& $NssmExe set "QuantBot-Dashboard" AppRestartDelay 3000

Write-Host "✅ NSSM 서비스 등록 완료."
Write-Host "시작:"
Write-Host "  net start QuantBot-MultiRunner"
Write-Host "  net start QuantBot-Dashboard"
Write-Host ""
Write-Host "중지:"
Write-Host "  net stop QuantBot-MultiRunner"
Write-Host "  net stop QuantBot-Dashboard"
