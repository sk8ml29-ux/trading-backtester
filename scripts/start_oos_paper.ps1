# Start all OOS paper bots (15m + 30m + 1h) — Windows
# Keep this PC on, or use VPS instead (see deploy/BETALA.md)
param(
    [double]$Capital = 20000,
    [double]$Risk = 0.0075
)

$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
New-Item -ItemType Directory -Force -Path "data\live" | Out-Null

$bots = @(
    @{ Tf = "15m"; Poll = 120 },
    @{ Tf = "30m"; Poll = 180 },
    @{ Tf = "1h";  Poll = 300 }
)

Write-Host "=== OOS Paper Bots (crypto, Binance data) ===" 
Write-Host "Startar 3 processer. Loggar: data\live\vps_bot_*.log"
Write-Host "Veckorapport: py scripts\paper_report.py`n"

foreach ($b in $bots) {
    $tf = $b.Tf
    $poll = $b.Poll
    $log = "data\live\vps_bot_${tf}.log"
    $errLog = "data\live\vps_bot_${tf}.err.log"
    $args = @(
        "run_live.py",
        "--optimized", "--portfolio", "--oos",
        "--timeframe", $tf,
        "--capital", $Capital,
        "--risk", $Risk,
        "--poll", $poll
    )
    Start-Process -FilePath "python" -ArgumentList $args -WorkingDirectory $root `
        -RedirectStandardOutput $log -RedirectStandardError $errLog -WindowStyle Hidden
    Write-Host "Started bot $tf (poll ${poll}s) -> $log"
}

Write-Host "`nKor: py scripts\paper_report.py"
Write-Host "Stoppa: Get-Process python | Stop-Process  (stanger ALLA python - var forsiktig)"
