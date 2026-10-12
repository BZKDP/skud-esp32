# Запуск серверного счётчика людей в while-цикле с авто-рестартом (задача skud-people).
$ErrorActionPreference = 'SilentlyContinue'
$base = 'C:\Users\Administrator\Desktop\skud\people'
$py = Join-Path $base 'venv\Scripts\python.exe'
$script = Join-Path $base 'people_server.py'
Set-Location $base   # чтобы YOLO нашёл локальный yolov8s.pt и bytetrack
while ($true) {
    try {
        & $py $script *>> (Join-Path $base 'people.log')
    } catch {}
    Start-Sleep -Seconds 5
}
