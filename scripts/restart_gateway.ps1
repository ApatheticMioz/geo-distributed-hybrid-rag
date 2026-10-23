# Restart the node_B gateway as a Task Scheduler job (survives SSH teardown).
# The task's root process is cmd.exe with built-in redirection, so the tree
# stays alive while uvicorn runs; `schtasks /End /TN pdc_gateway` stops it.
# Logs: gateway_out.log (stdout+stderr, append) in the repo root.
param(
    [string]$RepoPath = ""
)

$ErrorActionPreference = "Stop"

if (-not $RepoPath) {
    if ($env:PROJECT_ROOT) {
        $RepoPath = $env:PROJECT_ROOT
    } else {
        $RepoPath = Split-Path -Parent $PSScriptRoot
    }
}

$Log = Join-Path $RepoPath "gateway_out.log"
$PythonExe = Join-Path $RepoPath "systems\node_b\implementation\.venv\Scripts\python.exe"
if (-not (Test-Path $PythonExe)) {
    $PythonExe = "python.exe"
}

$ImplDir = Join-Path $RepoPath "systems\node_b\implementation"
$TR = "cmd /c set QDRANT_HOST=127.0.0.1 && cd /d ""$ImplDir"" && ""$PythonExe"" -m uvicorn src.server:app --host 0.0.0.0 --port 8000 >> ""$Log"" 2>&1"

# Kill any previous instance (task tree first, then stray :8000 listeners).
schtasks /End /TN pdc_gateway 2>$null | Out-Null
Start-Sleep -Seconds 1
$existing = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue
if ($existing) {
    $existing | Select-Object -ExpandProperty OwningProcess -Unique | ForEach-Object {
        Write-Output "Stopping stray gateway PID $_"
        Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue
    }
    Start-Sleep -Seconds 2
}

schtasks /Create /TN pdc_gateway /TR $TR /SC ONCE /ST 23:59 /F | Out-Null
schtasks /Run /TN pdc_gateway | Out-Null
Write-Output "Gateway task launched in $RepoPath; tail $Log for startup progress."
