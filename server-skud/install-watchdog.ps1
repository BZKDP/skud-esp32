# Registers the skud-watchdog scheduled task (runs watchdog.ps1 forever, as SYSTEM,
# at boot, restarts itself if it ever dies). ASCII-only. Run once on the server.

$ErrorActionPreference = 'Stop'
$root   = 'C:\Users\Administrator\Desktop\skud'
$script = Join-Path $root 'watchdog.ps1'

if (-not (Test-Path $script)) { throw "watchdog.ps1 not found at $script" }

$action = New-ScheduledTaskAction -Execute 'powershell.exe' `
    -Argument ('-ExecutionPolicy Bypass -NonInteractive -WindowStyle Hidden -File "{0}"' -f $script)

$trigger = New-ScheduledTaskTrigger -AtStartup

$principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest

$settings = New-ScheduledTaskSettingsSet `
    -MultipleInstances IgnoreNew `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero)

Register-ScheduledTask -TaskName 'skud-watchdog' -Action $action -Trigger $trigger `
    -Principal $principal -Settings $settings -Force | Out-Null

Start-ScheduledTask -TaskName 'skud-watchdog'
Start-Sleep -Seconds 2
$st = (Get-ScheduledTask -TaskName 'skud-watchdog').State
"skud-watchdog registered; state=$st"
