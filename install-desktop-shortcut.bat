@echo off
setlocal
cd /d "%~dp0"

if not exist "%~dp0assets\pendant.ico" (
  echo Missing assets\pendant.ico
  pause
  exit /b 1
)

set "SHORTCUT=%USERPROFILE%\Desktop\Mach3 Pendant.lnk"
set "TARGET=%~dp0run.bat"
set "WORKDIR=%~dp0."
set "ICON=%~dp0assets\pendant.ico"

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:SHORTCUT);" ^
  "$s.TargetPath = $env:TARGET;" ^
  "$s.WorkingDirectory = $env:WORKDIR;" ^
  "$s.IconLocation = $env:ICON;" ^
  "$s.Description = 'Mach3 Tablet Pendant';" ^
  "$s.Save()"

if errorlevel 1 (
  echo Failed to create desktop shortcut.
  pause
  exit /b 1
)

echo Created: %SHORTCUT%
echo Start Mach3, then double-click that icon.
pause
