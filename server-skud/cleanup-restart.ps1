$ErrorActionPreference = 'Continue'
# 1) Defender: не сканировать постоянно ML-папку счётчика (она грузила Defender при инсталле)
try { Add-MpPreference -ExclusionPath 'C:\Users\Administrator\Desktop\skud\people' -ErrorAction Stop; 'defender excl: added' } catch { 'defender excl: ' + $_.Exception.Message }
# 2) убрать разовые задачи от тестов
foreach ($t in 'skud-ov-install','skud-people-install','skud-people-test') { schtasks /delete /tn $t /f *>$null }
'leftover tasks: cleaned'
# 3) включить автозадачу камер обратно
schtasks /change /tn skud-mediamtx /enable *>$null
# 4) убить старый релончер mediamtx + сам mediamtx (перезапуск с новым конфигом)
Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" | Where-Object { $_.CommandLine -like '*run-skud-mediamtx*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Stop-Process -Name mediamtx -Force -ErrorAction SilentlyContinue
Start-Sleep 2
# 5) запустить заново
schtasks /run /tn skud-mediamtx *>$null
Start-Sleep 8
# 6) отчёт
$os = Get-CimInstance Win32_OperatingSystem
'RAM free MB: ' + [int]($os.FreePhysicalMemory/1KB)
(Get-Process mediamtx -ErrorAction SilentlyContinue | ForEach-Object { 'mediamtx pid=' + $_.Id + ' MB=' + [int]($_.WorkingSet64/1MB) })
$h = @{ Authorization = ('Basic ' + [Convert]::ToBase64String([Text.Encoding]::ASCII.GetBytes("camview:$($env:SKUD_MTX_PASS)"))) }
try { $p = ConvertFrom-Json (Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:9998/v3/paths/list' -Headers $h -TimeoutSec 8).Content; foreach ($x in $p.items) { 'cam ' + $x.name + ' ready=' + $x.ready } } catch { 'MTX API: ' + $_.Exception.Message }
try { 'HEALTH ' + (Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8097/api/health -TimeoutSec 5).StatusCode } catch { 'HEALTH DOWN' }
