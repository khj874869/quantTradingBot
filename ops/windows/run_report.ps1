param(
  [string]$StateDir = "state",
  [int]$Days = 30,
  [string]$AccountTag = "",
  [string]$Venue = "",
  [string]$Symbol = ""
)

$ArgsList = @("--state-dir", $StateDir, "--days", $Days)
if ($AccountTag -ne "") { $ArgsList += @("--account-tag", $AccountTag) }
if ($Venue -ne "") { $ArgsList += @("--venue", $Venue) }
if ($Symbol -ne "") { $ArgsList += @("--symbol", $Symbol) }

python -m quantbot.reporting.auto_report @ArgsList
