param([string]$Port = 'COM7', [int]$Seconds = 15)
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$p = New-Object System.IO.Ports.SerialPort $Port,115200,'None',8,'One'
$p.Encoding = [System.Text.Encoding]::UTF8
$p.ReadTimeout = 100
# Сделать reset платы: DTR=false, RTS=true (EN low) -> подождать -> RTS=false (EN high)
$p.DtrEnable = $false
$p.RtsEnable = $true
$p.Open()
Start-Sleep -Milliseconds 100
$p.RtsEnable = $false
Start-Sleep -Milliseconds 50
$p.DiscardInBuffer()

$buf = ''
$end = (Get-Date).AddSeconds($Seconds)
while ((Get-Date) -lt $end) {
  $chunk = $p.ReadExisting()
  if ($chunk) { $buf += $chunk }
  Start-Sleep -Milliseconds 200
}
$p.Close()
Write-Host "=== length:" $buf.Length "==="
$buf -split "`n" | Where-Object { $_ -notmatch 'wifi:sta is connecting' } | ForEach-Object { Write-Host $_ }
