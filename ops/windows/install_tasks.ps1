\
<#
Windows 작업 스케줄러에 "부팅 시 자동 실행 + 실패 시 재시작" 태스크를 등록합니다.

사용 방법(관리자 권한 PowerShell):
  cd ops\windows
  .\install_tasks.ps1 -ProjectRoot "C:\path\to\quantbot_complete_final"

주의:
- 본 스크립트는 schtasks 기반의 '단순' 등록입니다.
- 더 강력한 재시작/서비스형 운영은 NSSM 방식을 권장합니다(install_nssm_service.ps1 참고).
#>

param(
  [Parameter(Mandatory=$true)][string]$ProjectRoot,
  [string]$PythonExe = "python",
  [string]$MultiConfig = "example_multi_run.json",
  [int]$DashboardPort = 8899
)

$runMulti = "powershell.exe -ExecutionPolicy Bypass -File `"$ProjectRoot\ops\windows\run_multi_runner.ps1`" -ProjectRoot `"$ProjectRoot`" -ConfigPath `"$MultiConfig`" -PythonExe `"$PythonExe`""
$runDash  = "powershell.exe -ExecutionPolicy Bypass -File `"$ProjectRoot\ops\windows\run_dashboard.ps1`" -ProjectRoot `"$ProjectRoot`" -Port $DashboardPort -PythonExe `"$PythonExe`""

# ONSTART는 관리자 권한 필요할 수 있습니다. 권한 문제면 ONLOGON으로 바꿔도 됩니다.
schtasks /Create /F /TN "QuantBot-MultiRunner" /SC ONSTART /RL HIGHEST /TR $runMulti | Out-Null
schtasks /Create /F /TN "QuantBot-Dashboard"   /SC ONSTART /RL HIGHEST /TR $runDash  | Out-Null

Write-Host "✅ 작업 스케줄러 태스크 등록 완료:"
Write-Host " - QuantBot-MultiRunner"
Write-Host " - QuantBot-Dashboard"
Write-Host ""
Write-Host "삭제:"
Write-Host "  schtasks /Delete /F /TN QuantBot-MultiRunner"
Write-Host "  schtasks /Delete /F /TN QuantBot-Dashboard"
