# Start OOS forex paper bots (30m + 1h) — 20 000 SEK per bot
param(
    [double]$Capital = 20000,
    [double]$Risk = 0.0075
)

$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
New-Item -ItemType Directory -Force -Path "data\live" | Out-Null

$bots = @(
    @{ Tf = "30m"; Poll = 180 },
    @{ Tf = "1h";  Poll = 300 }
)

Write-Host "=== OOS Forex Paper Bots (Dukascopy cache) ==="
Write-Host "Startar 2 processer. Loggar: data\live\vps_bot_forex*.log"
Write-Host "Rapport: python scripts\forex_paper_report.py`n"

foreach ($b in $bots) {
    $tf = $b.Tf
    $poll = $b.Poll
    $log = "data\live\vps_bot_forex_${tf}.log"
    $errLog = "data\live\vps_bot_forex_${tf}.err.log"
    $args = @(
        "run_live.py",
        "--optimized", "--portfolio", "--forex",
        "--timeframe", $tf,
        "--capital", $Capital,
        "--risk", $Risk,
        "--poll", $poll
    )
    Start-Process -FilePath "python" -ArgumentList $args -WorkingDirectory $root `
        -RedirectStandardOutput $log -RedirectStandardError $errLog -WindowStyle Hidden
    Write-Host "Started forex bot $tf (poll ${poll}s) -> $log"
}

Write-Host "`nKor: python scripts\forex_paper_report.py"
