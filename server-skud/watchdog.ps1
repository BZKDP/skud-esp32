# SKUD site watchdog: checks http://127.0.0.1:8097 locally; if the site is truly
# unresponsive several times in a row, restarts it (kills uvicorn -> run.ps1
# supervisor respawns; if the supervisor itself died, restarts the skud-site task).
# ASCII-only on purpose (Windows PowerShell 5.1 mangles Cyrillic .ps1 without BOM).

$root       = Split-Path -Parent $MyInvocation.MyCommand.Path
$logsDir    = Join-Path $root 'logs'
if (-not (Test-Path $logsDir)) { New-Item -ItemType Directory -Path $logsDir | Out-Null }
$log        = Join-Path $logsDir 'watchdog.log'

$url            = 'http://127.0.0.1:8097/'
$intervalSec    = 20      # pause between checks
$timeoutSec     = 8       # a hung server does not answer within this -> counts as a fail
$failThreshold  = 3       # consecutive fails before we restart (~40-60s of downtime)
$cooldownSec    = 90      # after a restart, give it time before checking again

function Log($m) {
    $ts = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
    Add-Content -Path $log -Value ("[{0}] {1}" -f $ts, $m)
}

function Test-Site {
    try {
        $r = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec $timeoutSec
        return ($r.StatusCode -eq 200)
    } catch {
        return $false
    }
}

function Restart-Skud {
    Log 'SITE DOWN -> restarting skud'
    try {
        # kill every uvicorn (both the venv launcher and the Python311 worker share this cmdline)
        Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
            Where-Object { $_.CommandLine -like '*uvicorn main:app*' } |
            ForEach-Object {
                Log ("killing uvicorn PID " + $_.ProcessId)
                Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
            }
        Start-Sleep -Seconds 3
        # if the run.ps1 supervisor loop itself died, make sure the task is running again
        $t = Get-ScheduledTask -TaskName 'skud-site' -ErrorAction SilentlyContinue
        if ($t -and $t.State -ne 'Running') {
            Start-ScheduledTask -TaskName 'skud-site'
            Log 'skud-site task was not running -> started it'
        }
    } catch {
        Log ("restart error: " + $_.Exception.Message)
    }
}

Log 'watchdog started'
$fails = 0
while ($true) {
    if (Test-Site) {
        if ($fails -gt 0) { Log ("recovered (after {0} fail(s))" -f $fails) }
        $fails = 0
    } else {
        $fails++
        Log ("check failed ({0}/{1})" -f $fails, $failThreshold)
        if ($fails -ge $failThreshold) {
            Restart-Skud
            $fails = 0
            Log ("cooldown {0}s" -f $cooldownSec)
            Start-Sleep -Seconds $cooldownSec
        }
    }
    Start-Sleep -Seconds $intervalSec
}
