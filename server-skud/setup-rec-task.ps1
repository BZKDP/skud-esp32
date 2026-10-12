$ErrorActionPreference = 'Stop'
$act = New-ScheduledTaskAction -Execute 'C:\Users\Administrator\Desktop\skud\run-rec-upload.bat'
$trg = New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes 10)
$set = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -ExecutionTimeLimit ([TimeSpan]::Zero)
Register-ScheduledTask -TaskName 'skud-rec-upload' -Action $act -Trigger $trg -Settings $set -User 'Administrator' -Password $env:SKUD_TASK_PASSWORD -RunLevel Highest -Force | Out-Null
'task registered with IgnoreNew'
Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object { $_.CommandLine -like '*s3_uploader*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Start-Sleep 3
Start-ScheduledTask -TaskName 'skud-rec-upload'
Start-Sleep 16
'up=' + @(Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object { $_.CommandLine -like '*s3_uploader*' }).Count
