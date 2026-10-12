# ТЕСТ серверного счётчика (изолированно): картинка в counter_test, БЕЗ записи в статистику.
# Запускается разовой задачей skud-people-test (переживает обрыв SSH). Лог -> test.log.
$ErrorActionPreference = 'SilentlyContinue'
$base = 'C:\Users\Administrator\Desktop\skud\people'
$env:SKUD_VIZ_CAM = 'counter_test'
$env:SKUD_REPORT = '0'
Set-Location $base
Remove-Item (Join-Path $base 'test.log') -ErrorAction SilentlyContinue
& "$base\venv\Scripts\python.exe" "$base\people_server.py" *>> (Join-Path $base 'test.log')
