# Ставит окружение серверного счётчика (CPU torch + ultralytics) и качает веса.
# Запускается разовой задачей skud-people-install, чтобы пережить обрывы SSH.
$ErrorActionPreference = 'Continue'
$base = 'C:\Users\Administrator\Desktop\skud\people'
$log = Join-Path $base 'install.log'
$marker = Join-Path $base 'install.done'
Remove-Item $marker -ErrorAction SilentlyContinue
"=== start $(Get-Date) ===" | Out-File $log
$basepy = 'C:\Users\Administrator\Desktop\skud\.venv\Scripts\python.exe'
& $basepy -m venv (Join-Path $base 'venv') *>> $log
$py = Join-Path $base 'venv\Scripts\python.exe'
& $py -m pip install --upgrade pip *>> $log 2>&1
"=== torch cpu ===" | Out-File $log -Append
& $py -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu *>> $log 2>&1
"=== ultralytics ===" | Out-File $log -Append
& $py -m pip install ultralytics opencv-python *>> $log 2>&1
"=== weights ===" | Out-File $log -Append
Set-Location $base
& $py -c "from ultralytics import YOLO; YOLO('yolov8s.pt'); print('weights ok')" *>> $log 2>&1
"DONE $(Get-Date)" | Out-File $marker
"=== finished $(Get-Date) ===" | Out-File $log -Append
