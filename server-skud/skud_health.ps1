# SKUD health — single-shot status of the whole stack. ASCII-only (PS 5.1 safe).
# Run:  powershell -NoProfile -ExecutionPolicy Bypass -File skud_health.ps1
$ErrorActionPreference = 'SilentlyContinue'
$r = 'C:\Users\Administrator\Desktop\skud'
function cnt($name, $like) { (Get-CimInstance Win32_Process -Filter "Name='$name'" | Where-Object { $_.CommandLine -like $like } | Measure-Object).Count }

# --- resources ---
$cpu = (Get-CimInstance Win32_Processor | Measure-Object -Property LoadPercentage -Average).Average
$os = Get-CimInstance Win32_OperatingSystem
$ramFree = [math]::Round($os.FreePhysicalMemory / 1MB, 1)
$diskFree = [math]::Round((Get-PSDrive C).Free / 1GB, 1)
Write-Output ("RESOURCES  : CPU={0}%  RAM_free={1}GB  diskC_free={2}GB" -f $cpu, $ramFree, $diskFree)

# --- processes ---
$mtx = cnt 'mediamtx.exe' '*skud*'
$sup = cnt 'powershell.exe' '*run-skud-mediamtx*'
$up  = cnt 'python.exe' '*s3_uploader*'
$site = cnt 'python.exe' '*uvicorn main:app*'
Write-Output ("PROCESSES  : mediamtx={0} supervisor={1} uploader={2} site_uvicorn={3}" -f $mtx, $sup, $up, $site)

# --- ports ---
$ports = (Get-NetTCPConnection -State Listen | Where-Object { $_.LocalPort -in 1936, 8898, 8889, 9998, 8097 } | Select-Object -ExpandProperty LocalPort | Sort-Object -Unique) -join ','
Write-Output ("PORTS_LSTN : {0}" -f $ports)

# --- cameras (HLS server-side with camview auth) ---
$b = 'Basic ' + [Convert]::ToBase64String([Text.Encoding]::ASCII.GetBytes("camview:$($env:SKUD_MTX_PASS)"))
$cam = ''
foreach ($c in 'outdoor', 'sklad', 'pack') {
  try { Invoke-WebRequest ("http://127.0.0.1:8898/$c/index.m3u8") -Headers @{Authorization = $b } -UseBasicParsing -TimeoutSec 4 | Out-Null; $cam += " $c=LIVE" }
  catch { $cam += " $c=DOWN" }
}
Write-Output ("CAMERAS    :{0}" -f $cam)

# --- site ---
try { $sc = (Invoke-WebRequest 'http://127.0.0.1:8097/' -UseBasicParsing -TimeoutSec 5).StatusCode } catch { $sc = 'DOWN' }
Write-Output ("SITE       : http_8097={0}" -f $sc)

# --- recording (local folders; 5-min segments) ---
$rec = ''
foreach ($c in 'outdoor', 'sklad', 'pack') {
  $d = "$r\media\rec\$c"
  $fs = [IO.Directory]::GetFiles($d, '*.mp4')
  $last = ($fs | ForEach-Object { [IO.File]::GetLastWriteTime($_) } | Measure-Object -Maximum).Maximum
  $ls = if ($last) { $last.ToString('HH:mm:ss') } else { '-' }
  $rec += " $c(files=$($fs.Count),last=$ls)"
}
Write-Output ("RECORDING  :{0}" -f $rec)

# --- tasks + uploader log freshness ---
Write-Output ("TASKS      : mediamtx={0} site={1} upload={2} watchdog={3}" -f (Get-ScheduledTask skud-mediamtx).State, (Get-ScheduledTask skud-site).State, (Get-ScheduledTask skud-rec-upload).State, (Get-ScheduledTask skud-watchdog).State)
$ulog = "$r\media\logs\rec-upload.log"
if (Test-Path $ulog) { Write-Output ("UPLOAD_LOG : last_write={0}" -f (Get-Item $ulog).LastWriteTime) }
Write-Output ("TIME_NOW   : {0}" -f (Get-Date).ToString('yyyy-MM-dd HH:mm:ss'))
