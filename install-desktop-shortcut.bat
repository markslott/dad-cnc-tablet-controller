@echo off
setlocal
cd /d "%~dp0"

if not exist "%~dp0assets\pendant.ico" (
  echo Missing assets\pendant.ico
  pause
  exit /b 1
)

set "TARGET=%~dp0run.bat"
set "WORKDIR=%~dp0."
set "ICON=%~dp0assets\pendant.ico"

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$desktop = [Environment]::GetFolderPath('DesktopDirectory');" ^
  "if (-not $desktop) { $desktop = [Environment]::GetFolderPath('Desktop') };" ^
  "if (-not $desktop) { throw 'Could not resolve Desktop folder' };" ^
  "if (-not (Test-Path -LiteralPath $desktop)) { New-Item -ItemType Directory -Force -LiteralPath $desktop | Out-Null };" ^
  "$path = Join-Path $desktop 'Mach3 Pendant.lnk';" ^
  "$s = (New-Object -ComObject WScript.Shell).CreateShortcut($path);" ^
  "$s.TargetPath = $env:TARGET;" ^
  "$s.WorkingDirectory = $env:WORKDIR;" ^
  "$s.IconLocation = $env:ICON;" ^
  "$s.Description = 'Mach3 Tablet Pendant';" ^
  "$s.Save();" ^
  "Write-Host ('Created: ' + $path)"

if errorlevel 1 (
  echo Failed to create desktop shortcut.
  pause
  exit /b 1
)

echo Start Mach3, then double-click that icon.
pause
