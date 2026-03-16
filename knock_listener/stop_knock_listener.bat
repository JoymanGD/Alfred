@echo off
setlocal
cd /d "%~dp0"

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$listenerDir = (Resolve-Path '%~dp0').Path; $lockFile = Join-Path $listenerDir 'alfred_knock_listener.lock'; $escaped = [WildcardPattern]::Escape((Join-Path $listenerDir 'alfred_knock_listener.py')); $procs = Get-CimInstance Win32_Process | Where-Object { $_.ProcessId -ne $PID -and $_.CommandLine -and ($_.CommandLine -like ('*' + $escaped + '*') -or $_.CommandLine -like '*knock_listener\\alfred_knock_listener.py*') }; foreach ($p in $procs) { Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue }; Remove-Item -Force -ErrorAction SilentlyContinue $lockFile"
