@echo off
setlocal
cd /d "%~dp0"
if exist "runtime\pythonw.exe" (
  start "" "runtime\pythonw.exe" -s "%~dp0main.py"
) else (
  pyw -3.12 -s "%~dp0main.py"
)
endlocal
