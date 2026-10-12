param([string]$Port = 'COM7', [int]$Seconds = 12)
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$p = New-Object System.IO.Ports.SerialPort $Port,115200,'None',8,'One'
$p.Encoding = [System.Text.Encoding]::UTF8
$p.ReadTimeout = 100
$p.DtrEnable = $false
$p.RtsEnable = $false
$p.Open()
Start-Sleep -Milliseconds 200
$p.DiscardInBuffer()

# Жмём open
try {
  $r = Invoke-RestMethod -Uri ("http://" + $env:SKUD_SERVER_HOST + ":8097/api/open") -Method Post -Headers @{ 'X-Pin' = $env:SKUD_DOOR_PIN } -TimeoutSec 5
  Write-Host "[OPEN] response:" ($r | ConvertTo-Json -Compress)
} catch {
  Write-Host "[OPEN] error:" $_.Exception.Message
}

$buf = ''
$end = (Get-Date).AddSeconds($Seconds)
while ((Get-Date) -lt $end) {
  $chunk = $p.ReadExisting()
  if ($chunk) { $buf += $chunk }
  Start-Sleep -Milliseconds 150
}
$p.Close()
Write-Host "=== Serial ($($buf.Length) bytes) ==="
$buf -split "`n" | Where-Object { $_ -notmatch 'wifi:sta is connecting' } | ForEach-Object { Write-Host $_ }
