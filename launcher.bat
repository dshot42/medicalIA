@echo off
setlocal
cd /d "%~dp0"
if exist "%~dp0.venv\Scripts\activate.bat" (
  call "%~dp0.venv\Scripts\activate.bat"
)
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0launcher.ps1" %*
if errorlevel 1 (
  echo.
  echo Le lancement a echoue. Consultez le message ci-dessus.
  pause
)
endlocal
