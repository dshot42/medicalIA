@echo off
setlocal
cd /d "%~dp0"
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0install-gpu.ps1" %*
if errorlevel 1 (
  echo.
  echo L'installation GPU a echoue. Consultez le message ci-dessus.
  pause
)
endlocal
