param(
    [string]$LogPath = "",
    [int]$IntervalSeconds = 5,
    [int]$TailLines = 200,
    [switch]$Once
)

$repoRoot = Split-Path -Parent $PSScriptRoot
if (-not $LogPath) {
    $LogPath = Join-Path $repoRoot "demo\results\logs\simulation_orchestrator.log"
}

Write-Host "Watching simulation progress: $LogPath"
Write-Host "Press Ctrl+C to stop."

$lastPrinted = ""
while ($true) {
    if (-not (Test-Path -LiteralPath $LogPath)) {
        Write-Host ("[{0}] waiting for log file..." -f (Get-Date -Format "HH:mm:ss"))
        Start-Sleep -Seconds $IntervalSeconds
        continue
    }

    $lines = Get-Content -LiteralPath $LogPath -Tail $TailLines -ErrorAction SilentlyContinue
    $interesting = $lines | Where-Object {
        $_ -match "\[STEP\]" -or
        $_ -match "driver loop begin" -or
        $_ -match "driver loop end" -or
        $_ -match "simulation run complete" -or
        $_ -match "evaluation failed"
    } | Select-Object -Last 1

    if ($interesting -and $interesting -ne $lastPrinted) {
        $lastPrinted = $interesting
        if ($interesting -match "\[STEP\]\s+driver=(\S+)\s+step=(\d+).*?sim_clock=([^\(]+)\s+\(min\s+(\d+)->(\d+)\).*?decision=(\S+).*?total=(\d+)") {
            $driver = $matches[1]
            $step = $matches[2]
            $clock = $matches[3].Trim()
            $minuteTo = $matches[5]
            $decision = $matches[6]
            $tokens = $matches[7]
            Write-Host ("[{0}] driver={1} step={2} sim={3} min={4} action={5} tokens={6}" -f (Get-Date -Format "HH:mm:ss"), $driver, $step, $clock, $minuteTo, $decision, $tokens)
        } else {
            Write-Host ("[{0}] {1}" -f (Get-Date -Format "HH:mm:ss"), $interesting)
        }
    } else {
        $file = Get-Item -LiteralPath $LogPath
        Write-Host ("[{0}] no new step; log updated {1}" -f (Get-Date -Format "HH:mm:ss"), $file.LastWriteTime.ToString("HH:mm:ss"))
    }

    if ($Once) {
        break
    }

    Start-Sleep -Seconds $IntervalSeconds
}
