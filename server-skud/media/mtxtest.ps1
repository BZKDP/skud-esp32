$r = 'C:\Users\Administrator\Desktop\skud\media'
Disable-ScheduledTask skud-mediamtx -ErrorAction SilentlyContinue | Out-Null
Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" | Where-Object { $_.CommandLine -like '*run-skud-mediamtx*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Get-CimInstance Win32_Process -Filter "Name='mediamtx.exe'" | Where-Object { $_.CommandLine -like '*skud*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Start-Sleep -Seconds 2
$out = Join-Path $r '_mtxtest.out'
$err = Join-Path $r '_mtxtest.err'
$p = Start-Process -FilePath (Join-Path $r 'mediamtx.exe') -ArgumentList 'skud-mediamtx.yml' -WorkingDirectory $r -RedirectStandardOutput $out -RedirectStandardError $err -WindowStyle Hidden -PassThru
Start-Sleep -Seconds 4
Write-Output ("mediamtx alive after 4s: " + (-not $p.HasExited) + "  exitcode: " + $p.ExitCode)
Write-Output "--- STDOUT head ---"
if (Test-Path $out) { Get-Content $out -TotalCount 30 }
Write-Output "--- STDERR head ---"
if (Test-Path $err) { Get-Content $err -TotalCount 30 }
