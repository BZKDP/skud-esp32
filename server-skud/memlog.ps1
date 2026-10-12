# Appends a memory snapshot every run (scheduled every 5 min) to catch what grows before OOM.
$os = Get-CimInstance Win32_OperatingSystem
$top = (Get-Process | Sort-Object PrivateMemorySize64 -Descending | Select-Object -First 6 | ForEach-Object { $_.Name + ':' + [int]($_.PrivateMemorySize64/1MB) }) -join ' '
$line = (Get-Date -Format 'MM-dd HH:mm') + ' freeMB=' + [int]($os.FreePhysicalMemory/1KB) + ' commitMB=' + [int](($os.TotalVirtualMemorySize - $os.FreeVirtualMemory)/1KB) + '/' + [int]($os.TotalVirtualMemorySize/1KB) + ' | ' + $top
Add-Content -Path 'C:\Users\Administrator\Desktop\skud\people\memlog.txt' -Value $line
