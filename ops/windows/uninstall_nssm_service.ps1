\
<#
NSSM 서비스 제거

사용(관리자 PowerShell):
  cd ops\windows
  .\uninstall_nssm_service.ps1
#>

param(
  [string]$NssmExe = "$PSScriptRoot\nssm.exe"
)

if (!(Test-Path $NssmExe)) {
  Write-Host "❌ nssm.exe 를 찾을 수 없습니다: $NssmExe"
  exit 1
}

# stop first (ignore errors)
net stop QuantBot-MultiRunner 2>$null | Out-Null
net stop QuantBot-Dashboard 2>$null | Out-Null

& $NssmExe remove "QuantBot-MultiRunner" confirm
& $NssmExe remove "QuantBot-Dashboard" confirm

Write-Host "✅ NSSM 서비스 제거 완료."
