$ErrorActionPreference = "Stop"

$baseUrl = "http://127.0.0.1:8080"
$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$outDir = Join-Path $PSScriptRoot "agent1_agent4_final_control_proof_$stamp"
New-Item -ItemType Directory -Force -Path $outDir | Out-Null

function Get-QfosStatus {
    Invoke-RestMethod "$baseUrl/status"
}

function Show-State {
    param([string]$Label, $Status)

    Write-Host "`n========== $Label ==========" -ForegroundColor Cyan
    Write-Host "bot_state=$($Status.bot_state)"
    Write-Host "paused=$($Status.paused)"
    Write-Host "pause_reason=$($Status.pause_reason)"
    Write-Host "loop_running=$($Status.trading_loop_running)"
    Write-Host "cycle_id=$($Status.runtime_cycle_id)"
    Write-Host "last_cycle=$($Status.trading_loop_last_cycle_at)"
    Write-Host "last_tick=$($Status.trading_loop_last_market_tick_at)"
    Write-Host "last_feature=$($Status.trading_loop_last_feature_update_at)"
    Write-Host "heartbeat_age=$($Status.trading_loop_heartbeat_age_seconds)"
    Write-Host "runtime_loop_stale=$($Status.runtime_loop_stale)"
    Write-Host "warnings=$($Status.runtime_anomaly_warnings -join ';')"
}

$logStart = Get-Date
$resumeIssued = $false
$pauseIssued = $false
$before = $null
$after = $null
$final = $null

try {
    $before = Get-QfosStatus
    $before | ConvertTo-Json -Depth 12 | Set-Content "$outDir\status_before.json" -Encoding UTF8
    Show-State "BEFORE RESUME" $before

    Write-Host "`nIssuing resume..." -ForegroundColor Yellow
    Invoke-RestMethod -Method Post "$baseUrl/resume" |
        ConvertTo-Json -Depth 10 |
        Tee-Object "$outDir\resume_response.json"

    $resumeIssued = $true

    Write-Host "`nWaiting 160 seconds for complete loop cycles..." -ForegroundColor Yellow
    Start-Sleep -Seconds 160

    $after = Get-QfosStatus
    $after | ConvertTo-Json -Depth 12 | Set-Content "$outDir\status_after_resume.json" -Encoding UTF8
    Show-State "AFTER RESUME" $after

    $beforeCycle = [int]($before.runtime_cycle_id)
    $afterCycle = [int]($after.runtime_cycle_id)
    $cycleIncrease = $afterCycle - $beforeCycle

    Write-Host "`n========== PRE-PAUSE VERDICT ==========" -ForegroundColor Cyan
    Write-Host "cycle_increase=$cycleIncrease"
    Write-Host "three_cycles_observed=$($cycleIncrease -ge 3)"
    Write-Host "last_cycle_populated=$(-not [string]::IsNullOrWhiteSpace([string]$after.trading_loop_last_cycle_at))"
    Write-Host "last_tick_populated=$(-not [string]::IsNullOrWhiteSpace([string]$after.trading_loop_last_market_tick_at))"
    Write-Host "last_feature_populated=$(-not [string]::IsNullOrWhiteSpace([string]$after.trading_loop_last_feature_update_at))"
    Write-Host "runtime_loop_stale=$($after.runtime_loop_stale)"
}
catch {
    Write-Host "`nTEST_ERROR=$($_.Exception.Message)" -ForegroundColor Red
    $_.Exception.Message | Set-Content "$outDir\test_error.txt" -Encoding UTF8
}
finally {
    try {
        Write-Host "`nIssuing pause..." -ForegroundColor Yellow

        Invoke-RestMethod -Method Post "$baseUrl/pause" |
            ConvertTo-Json -Depth 10 |
            Tee-Object "$outDir\pause_response.json"

        $pauseIssued = $true
        Start-Sleep -Seconds 15

        $final = Get-QfosStatus
        $final | ConvertTo-Json -Depth 12 | Set-Content "$outDir\status_final_paused.json" -Encoding UTF8
        Show-State "FINAL AFTER PAUSE" $final
    }
    catch {
        Write-Host "`nPAUSE_FAILURE=$($_.Exception.Message)" -ForegroundColor Red
        $_.Exception.Message | Set-Content "$outDir\pause_error.txt" -Encoding UTF8
    }
}

$logs = docker compose logs --since $logStart.ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ") quant
$logs | Set-Content "$outDir\current_lifecycle_logs.txt" -Encoding UTF8

$patterns = @(
    "QFOS_CONTROL_EVENT",
    "QFOS_LOOP_CONTROL",
    "QFOS_CYCLE",
    "QFOS_MARKET_TICK",
    "QFOS_FEATURE_CYCLE",
    "QFOS_QUALITY_CYCLE",
    "QFOS_EXECUTION_CYCLE",
    "Traceback",
    "Bot loop error",
    "SyntaxError",
    "ERROR"
)

Write-Host "`n========== LOG COUNTS ==========" -ForegroundColor Cyan
foreach ($pattern in $patterns) {
    $count = ($logs | Select-String -SimpleMatch $pattern).Count
    Write-Host "$pattern=$count"
}

$relevant = $logs | Select-String -Pattern "QFOS_CONTROL_EVENT|QFOS_LOOP_CONTROL|QFOS_CYCLE|QFOS_MARKET_TICK|QFOS_FEATURE_CYCLE|QFOS_QUALITY_CYCLE|QFOS_EXECUTION_CYCLE|Traceback|Bot loop error|SyntaxError|ERROR"
$relevant | Tee-Object "$outDir\relevant_logs.txt"

Write-Host "`n========== FINAL CONTROL VERDICT ==========" -ForegroundColor Cyan
Write-Host "resume_issued=$resumeIssued"
Write-Host "pause_issued=$pauseIssued"
Write-Host "final_paused=$($final.paused)"
Write-Host "final_bot_state=$($final.bot_state)"
Write-Host "final_pause_reason=$($final.pause_reason)"
Write-Host "Artifacts=$outDir"
