# Start paper trading bot — legacy wrapper; prefer start_oos_paper.ps1
param(
    [string]$Timeframe = "30m",
    [double]$Capital = 20000,
    [double]$Risk = 0.0075
)

$poll = if ($Timeframe -eq "15m") { 120 } elseif ($Timeframe -eq "1h") { 300 } else { 180 }
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

Write-Host "Starting OOS paper bot on $Timeframe (poll ${poll}s)"
Write-Host "Press Ctrl+C to stop.`n"

python run_live.py --optimized --portfolio --oos `
    --timeframe $Timeframe `
    --capital $Capital `
    --risk $Risk `
    --poll $poll
