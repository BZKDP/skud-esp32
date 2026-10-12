# Ставит OpenVINO и экспортирует yolov8s -> OpenVINO IR (быстрее на CPU). Разовая задача.
$ErrorActionPreference = 'Continue'
$base = 'C:\Users\Administrator\Desktop\skud\people'
$log = Join-Path $base 'ov.log'
$marker = Join-Path $base 'ov.done'
Remove-Item $marker -ErrorAction SilentlyContinue
"=== start $(Get-Date) ===" | Out-File $log
$py = Join-Path $base 'venv\Scripts\python.exe'
"=== pip openvino ===" | Out-File $log -Append
& $py -m pip install openvino *>> $log 2>&1
"=== export ===" | Out-File $log -Append
Set-Location $base
& $py -c "from ultralytics import YOLO; YOLO('yolov8s.pt').export(format='openvino'); print('EXPORT OK')" *>> $log 2>&1
"DONE $(Get-Date)" | Out-File $marker
"=== finished $(Get-Date) ===" | Out-File $log -Append
