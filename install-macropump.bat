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
echo After Mach3 is running with Run Macro Pump ticked, open:
echo   C:\Mach3\pendant-pump.log
echo If that file is missing, the script is not in this profile's macros folder.
echo Then start the pendant. Console should say macropump is talking.
pause
