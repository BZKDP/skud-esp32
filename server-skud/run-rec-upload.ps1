$ErrorActionPreference = 'SilentlyContinue'
# SINGLETON: задача перезапускается каждые 10 мин для самолечения; если обёртка уже
# работает - новый экземпляр тихо выходит (не плодим дубли).
$mutex = New-Object System.Threading.Mutex($false, 'Global\skud-rec-upload-wrapper')
if (-not $mutex.WaitOne(0)) { exit }
$base = 'C:\Users\Administrator\Desktop\skud'
$py = Join-Path $base '.venv\Scripts\python.exe'
$logs = Join-Path $base 'media\logs'
if (-not (Test-Path $logs)) { New-Item -ItemType Directory -Path $logs | Out-Null }
Set-Location $base
while ($true) {
    try { & $py (Join-Path $base 's3_uploader.py') *>> (Join-Path $logs 'rec-upload.log') } catch {}
    Start-Sleep -Seconds 5
}
