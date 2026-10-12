$ErrorActionPreference = 'Continue'
# Root fix: forbid the task from launching parallel instances.
try { $t = Get-ScheduledTask -TaskName 'skud-mediamtx'; $t.Settings.MultipleInstances = 'IgnoreNew'; Set-ScheduledTask -InputObject $t | Out-Null; 'policy: IgnoreNew set' } catch { 'policy set failed: ' + $_.Exception.Message }
# Multi-pass kill of all loops + mediamtx
for ($k = 0; $k -lt 5; $k++) {
  $mtx = Get-CimInstance Win32_Process -Filter "Name='mediamtx.exe'"
  $parents = @($mtx | Select-Object -ExpandProperty ParentProcessId -Unique)
  $loops = @(Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" | Where-Object { $_.CommandLine -like '*run-skud-mediamtx*' } | Select-Object -ExpandProperty ProcessId)
  foreach ($id in (@($parents) + @($loops) | Select-Object -Unique)) { Stop-Process -Id $id -Force -ErrorAction SilentlyContinue }
  Get-Process mediamtx -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
  Start-Sleep 2
}
'after kill: mediamtx=' + @(Get-Process mediamtx -ErrorAction SilentlyContinue).Count + ' loops=' + @(Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" | Where-Object { $_.CommandLine -like '*run-skud-mediamtx*' }).Count
schtasks /end /tn skud-mediamtx *>$null
Start-Sleep 1
schtasks /run /tn skud-mediamtx *>$null
Start-Sleep 25
'--- RESULT (after 25s window) ---'
'mediamtx=' + @(Get-Process mediamtx -ErrorAction SilentlyContinue).Count + ' loops=' + @(Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" | Where-Object { $_.CommandLine -like '*run-skud-mediamtx*' }).Count
Get-Process mediamtx -ErrorAction SilentlyContinue | Select-Object Id, @{n='MB';e={[int]($_.WorkingSet64/1MB)}} | Format-Table -AutoSize | Out-String
$h = @{ Authorization = ('Basic ' + [Convert]::ToBase64String([Text.Encoding]::ASCII.GetBytes("camview:$($env:SKUD_MTX_PASS)"))) }
try { $p = ConvertFrom-Json (Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:9998/v3/paths/list' -Headers $h -TimeoutSec 8).Content; foreach ($x in $p.items) { 'cam ' + $x.name + ' ready=' + $x.ready } } catch { 'MTX API: ' + $_.Exception.Message }
'RAM free MB: ' + [int]((Get-CimInstance Win32_OperatingSystem).FreePhysicalMemory/1KB)
try { 'HEALTH ' + (Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8097/api/health -TimeoutSec 5).StatusCode } catch { 'HEALTH DOWN' }
