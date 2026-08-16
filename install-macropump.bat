@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"

set "SRC=%~dp0mach3\macropump.m1s"
if not exist "%SRC%" (
  echo Missing mach3\macropump.m1s
  pause
  exit /b 1
)

set "MACHROOT=C:\Mach3"
if not exist "%MACHROOT%\macros" (
  echo Could not find %MACHROOT%\macros
  echo Copy mach3\macropump.m1s into C:\Mach3\macros\^<profile^>\ yourself.
  pause
  exit /b 1
)

echo Installing pendant macropump into Mach3 profile macro folders...
set FOUND=0
for /d %%D in ("%MACHROOT%\macros\*") do (
  set FOUND=1
  if exist "%%D\macropump.m1s" copy /y "%%D\macropump.m1s" "%%D\macropump.bak-pendant.m1s" >nul
  copy /y "%SRC%" "%%D\macropump.m1s" >nul
  echo   %%D\macropump.m1s
)

if "!FOUND!"=="0" (
  echo No profile folders under %MACHROOT%\macros
  pause
  exit /b 1
)

echo.
echo Enable it in Mach3 like this (the tick does not stick if you skip OK):
echo   1. Close Mach3 completely.
echo   2. Start Mach3. Look at the profile name in the lower-right corner.
echo      macropump.m1s must be in C:\Mach3\macros\^<that name^>\
echo   3. Config - General Config. Tick Run Macro Pump (third column).
echo   4. Click OK -- not the X, not Cancel.
echo   5. File - Exit Mach3, then start Mach3 again.
echo   6. Open General Config: the box should still be ticked. If not, tick OK
echo      and restart one more time (Mach3 sometimes needs two restarts).
echo.
echo If it unchecks itself after a restart, the script failed to load. Pull the
echo latest pendant files and run this installer again, then repeat the steps.
echo After a good start, C:\Mach3\pendant-pump.log should appear and keep updating.
pause
