$ErrorActionPreference = 'SilentlyContinue'
$os = Get-CimInstance Win32_OperatingSystem
'==== SYSTEM ===='
'Now: ' + (Get-Date -Format 'yyyy-MM-dd HH:mm:ss')
'Boot: ' + $os.LastBootUpTime
'Uptime: ' + ((Get-Date) - $os.LastBootUpTime).ToString()
'RAM total/free MB: ' + [int]($os.TotalVisibleMemorySize/1KB) + ' / ' + [int]($os.FreePhysicalMemory/1KB)
'Commit used/limit MB: ' + [int](($os.TotalVirtualMemorySize - $os.FreeVirtualMemory)/1KB) + ' / ' + [int]($os.TotalVirtualMemorySize/1KB)
Get-CimInstance Win32_PageFileUsage | ForEach-Object { 'Pagefile ' + $_.Name + ' alloc=' + $_.AllocatedBaseSize + 'MB current=' + $_.CurrentUsage + 'MB peak=' + $_.PeakUsage + 'MB' }

'==== TOP 15 PROCESSES by Private(commit) MB ===='
Get-Process | Sort-Object PrivateMemorySize64 -Descending | Select-Object -First 15 Name, Id, @{n='WS_MB';e={[int]($_.WorkingSet64/1MB)}}, @{n='Priv_MB';e={[int]($_.PrivateMemorySize64/1MB)}} | Format-Table -AutoSize | Out-String

'==== SVCHOST breakdown (priv MB + hosted services) ===='
$svc = Get-CimInstance Win32_Service | Where-Object { $_.ProcessId -gt 0 }
Get-Process svchost | Sort-Object PrivateMemorySize64 -Descending | Select-Object -First 10 | ForEach-Object {
  $procId = $_.Id
  $names = ($svc | Where-Object { $_.ProcessId -eq $procId } | Select-Object -ExpandProperty Name) -join ','
  'svchost pid=' + $procId + ' priv=' + [int]($_.PrivateMemorySize64/1MB) + 'MB ws=' + [int]($_.WorkingSet64/1MB) + 'MB svcs=[' + $names + ']'
}

'==== NODE / unexpected procs (cmdline) ===='
Get-CimInstance Win32_Process | Where-Object { $_.Name -in 'node.exe','java.exe','httpd.exe','nginx.exe' } | ForEach-Object { $_.Name + ' pid=' + $_.ProcessId + ' priv=' + [int]($_.PrivateMemorySize64/1MB) + 'MB cmd=' + ($_.CommandLine.Substring(0,[Math]::Min(120,$_.CommandLine.Length))) }

'==== Resource-Exhaustion history ===='
Get-WinEvent -FilterHashtable @{LogName='System'; ProviderName='Microsoft-Windows-Resource-Exhaustion-Detector'} -MaxEvents 8 | ForEach-Object { $_.TimeCreated.ToString('MM-dd HH:mm') + ': ' + (($_.Message -replace '\s+',' ')) }

'==== Application errors (Level=2) last 8 ===='
Get-WinEvent -FilterHashtable @{LogName='Application'; Level=2} -MaxEvents 8 | ForEach-Object { $m = ($_.Message -replace '\s+',' '); $_.TimeCreated.ToString('MM-dd HH:mm') + ' ' + $_.ProviderName + ': ' + $m.Substring(0,[Math]::Min(110,$m.Length)) }

'==== Non-Microsoft scheduled tasks (enabled) ===='
Get-ScheduledTask | Where-Object { $_.TaskPath -notlike '\Microsoft\*' -and $_.State -ne 'Disabled' } | Select-Object TaskName, State | Format-Table -AutoSize | Out-String

'==== Our procs ===='
Get-Process mediamtx, python -ErrorAction SilentlyContinue | Select-Object Name, Id, @{n='Priv_MB';e={[int]($_.PrivateMemorySize64/1MB)}} | Format-Table -AutoSize | Out-String
