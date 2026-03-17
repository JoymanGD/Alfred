@echo off
setlocal
cd /d "%~dp0"

> "%~dp0alfred_knock_listener.log" (
  rem Reset listener log on every launch.
)

set "PYW=%LocalAppData%\Programs\Python\Python311\pythonw.exe"
if exist "%PYW%" (
  powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -Command ^
    "Start-Process -FilePath '%PYW%' -WindowStyle Hidden -ArgumentList '\"%~dp0alfred_knock_listener.py\"' -WorkingDirectory '%~dp0'"
) else (
  powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -Command ^
    "Start-Process -FilePath 'python' -WindowStyle Hidden -ArgumentList '\"%~dp0alfred_knock_listener.py\"' -WorkingDirectory '%~dp0'"
)
