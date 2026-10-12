$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $false

# SINGLETON guard: do not spawn duplicate relaunchers (repeated /run or double trigger).
# If an instance is already running - exit quietly. Prevents a 2nd loop flapping mediamtx.
$mutex = New-Object System.Threading.Mutex($false, 'Global\skud-mediamtx-singleton')
# WaitOne на "брошенном" (abandoned) мьютексе кидает AbandonedMutexException, но ВЛАДЕНИЕ
# при этом ВЫДАЁТСЯ. Раньше с $ErrorActionPreference='Stop' это роняло супервизор в цикле
# (после force-kill предыдущего экземпляра) -> mediamtx вообще не поднимался. Ловим и продолжаем.
try { $acquired = $mutex.WaitOne(0) } catch [System.Threading.AbandonedMutexException] { $acquired = $true }
if (-not $acquired) { exit }

$serviceDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$logsDir = Join-Path $serviceDir 'logs'
$exePath = Join-Path $serviceDir 'mediamtx.exe'
$configPath = Join-Path $serviceDir 'skud-mediamtx.yml'
$stdoutLog = Join-Path $logsDir 'skud-mediamtx.out.log'
$stderrLog = Join-Path $logsDir 'skud-mediamtx.err.log'

if (-not (Test-Path $logsDir)) { New-Item -ItemType Directory -Path $logsDir | Out-Null }
Set-Location $serviceDir

while ($true) {
  Add-Content -Path $stdoutLog -Value ("[{0}] starting skud-mediamtx" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'))
  $outTmp = Join-Path $logsDir ("mtx-{0}.out.tmp" -f ([guid]::NewGuid().ToString('N')))
  $errTmp = Join-Path $logsDir ("mtx-{0}.err.tmp" -f ([guid]::NewGuid().ToString('N')))
  $process = Start-Process -FilePath $exePath -ArgumentList $configPath -WorkingDirectory $serviceDir `
    -RedirectStandardOutput $outTmp -RedirectStandardError $errTmp -WindowStyle Hidden -PassThru
  $process.WaitForExit()
  if (Test-Path $outTmp) { Get-Content $outTmp | Add-Content $stdoutLog; Remove-Item $outTmp -Force -ErrorAction SilentlyContinue }
  if (Test-Path $errTmp) { Get-Content $errTmp | Add-Content $stderrLog; Remove-Item $errTmp -Force -ErrorAction SilentlyContinue }
  Add-Content -Path $stderrLog -Value ("[{0}] skud-mediamtx exited code {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $process.ExitCode)
  Start-Sleep -Seconds 5
}
