$ErrorActionPreference = 'Continue'
'=== pagefile -> fixed 12GB (commit headroom; applies after reboot) ==='
try {
  $cs = Get-WmiObject Win32_ComputerSystem -EnableAllPrivileges
  if ($cs.AutomaticManagedPagefile) { $cs.AutomaticManagedPagefile = $false; [void]$cs.Put() }
  $pf = Get-WmiObject Win32_PageFileSetting
  if ($pf) { $pf.InitialSize = 12288; $pf.MaximumSize = 12288; [void]$pf.Put(); 'pagefile: updated existing to 12GB' }
  else { Set-WmiInstance -Class Win32_PageFileSetting -Arguments @{Name='C:\pagefile.sys'; InitialSize=12288; MaximumSize=12288} | Out-Null; 'pagefile: created 12GB fixed' }
} catch { 'pagefile err: ' + $_.Exception.Message }

'=== Defender exclusions (cut scanning load) ==='
try { Add-MpPreference -ExclusionPath 'C:\Users\Administrator\Desktop' -ErrorAction Stop; 'excl path: Desktop' } catch { 'excl path err: ' + $_.Exception.Message }
try { Add-MpPreference -ExclusionProcess 'node.exe','mediamtx.exe','python.exe','ffmpeg.exe' -ErrorAction Stop; 'excl procs: node/mediamtx/python/ffmpeg' } catch { 'excl procs err: ' + $_.Exception.Message }

'=== verify ==='
Get-WmiObject Win32_PageFileSetting | ForEach-Object { 'PF setting ' + $_.Name + ' init=' + $_.InitialSize + ' max=' + $_.MaximumSize }
'AutoManaged now=' + (Get-WmiObject Win32_ComputerSystem).AutomaticManagedPagefile
'ExclusionPaths=' + ((Get-MpPreference).ExclusionPath -join ', ')
