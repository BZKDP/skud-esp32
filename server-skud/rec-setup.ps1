$ErrorActionPreference = 'Continue'
New-Item -ItemType Directory -Force 'C:\Users\Administrator\Desktop\skud\media\rec' | Out-Null
# restart ONLY skud mediamtx (its relauncher will start it again with the new record config)
Get-CimInstance Win32_Process -Filter "Name='mediamtx.exe'" | Where-Object { $_.CommandLine -like '*skud-mediamtx.yml*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Start-Sleep 10
$m = @(Get-CimInstance Win32_Process -Filter "Name='mediamtx.exe'" | Where-Object { $_.CommandLine -like '*skud-mediamtx.yml*' })
'skud mediamtx count=' + $m.Count
$h = @{ Authorization = ('Basic ' + [Convert]::ToBase64String([Text.Encoding]::ASCII.GetBytes("camview:$($env:SKUD_MTX_PASS)"))) }
try { $p = ConvertFrom-Json (Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:9998/v3/paths/list' -Headers $h -TimeoutSec 8).Content; foreach ($x in $p.items) { 'cam ' + $x.name + ' ready=' + $x.ready } } catch { 'MTX API: ' + $_.Exception.Message }
'freeMB=' + [int]((Get-CimInstance Win32_OperatingSystem).FreePhysicalMemory/1KB)
