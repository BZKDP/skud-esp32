param([string]$Port = 'COM7', [int]$Seconds = 15)
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$p = New-Object System.IO.Ports.SerialPort $Port,115200,'None',8,'One'
$p.Encoding = [System.Text.Encoding]::UTF8
$p.ReadTimeout = 100
$p.DtrEnable = $false
$p.RtsEnable = $false
$p.Open()
$buf = ''
$end = (Get-Date).AddSeconds($Seconds)
while ((Get-Date) -lt $end) {
  $chunk = $p.ReadExisting()
  if ($chunk) { $buf += $chunk }
  Start-Sleep -Milliseconds 200
}
$p.Close()
$buf -split "`n" | Where-Object { $_ -notmatch 'wifi:sta is connecting' } | ForEach-Object { Write-Host $_ }
