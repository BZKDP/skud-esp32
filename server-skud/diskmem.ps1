$ErrorActionPreference = 'SilentlyContinue'
$os = Get-CimInstance Win32_OperatingSystem
'== MEM now =='
'freeMB=' + [int]($os.FreePhysicalMemory/1KB) + ' commitMB=' + [int](($os.TotalVirtualMemorySize - $os.FreeVirtualMemory)/1KB) + '/' + [int]($os.TotalVirtualMemorySize/1KB)
'== memlog trend (last 16) =='
Get-Content 'C:\Users\Administrator\Desktop\skud\people\memlog.txt' -Tail 16 -ErrorAction SilentlyContinue
$d = Get-CimInstance Win32_LogicalDisk -Filter "DeviceID='C:'"
'== DISK C: free GB=' + [math]::Round($d.FreeSpace/1GB,2) + ' of ' + [math]::Round($d.Size/1GB,1) + ' =='
'== top dirs under Desktop (GB) =='
Get-ChildItem 'C:\Users\Administrator\Desktop' -Directory -ErrorAction SilentlyContinue | ForEach-Object {
  $sz = (Get-ChildItem $_.FullName -Recurse -File -Force -ErrorAction SilentlyContinue | Measure-Object Length -Sum).Sum
  [PSCustomObject]@{ Dir = $_.Name; GB = [math]::Round($sz/1GB,2) }
} | Sort-Object GB -Descending | Select-Object -First 14 | Format-Table -AutoSize | Out-String
'== files > 300MB anywhere under Desktop =='
Get-ChildItem 'C:\Users\Administrator\Desktop' -Recurse -File -Force -ErrorAction SilentlyContinue | Where-Object { $_.Length -gt 300MB } | Sort-Object Length -Descending | Select-Object -First 15 @{n='MB';e={[int]($_.Length/1MB)}}, FullName | Format-Table -AutoSize | Out-String
'== our recordings local (media\rec) =='
$rec = Get-ChildItem 'C:\Users\Administrator\Desktop\skud\media\rec' -Recurse -File -ErrorAction SilentlyContinue
'count=' + @($rec).Count + ' sizeMB=' + [int]((($rec | Measure-Object Length -Sum).Sum)/1MB)
'== big LOG files (>30MB) =='
Get-ChildItem 'C:\Users\Administrator\Desktop' -Recurse -File -Include *.log,*.txt -ErrorAction SilentlyContinue | Where-Object { $_.Length -gt 30MB } | Sort-Object Length -Descending | Select-Object -First 12 @{n='MB';e={[int]($_.Length/1MB)}}, FullName | Format-Table -AutoSize | Out-String
