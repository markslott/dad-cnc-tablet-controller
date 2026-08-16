@echo off
setlocal
cd /d "%~dp0"

if not defined MACH3_BACKEND set MACH3_BACKEND=pump
if not defined MACH3_HOST set MACH3_HOST=0.0.0.0
if not defined MACH3_PORT set MACH3_PORT=8080
if not defined MACH3_MODBUS_HOST set MACH3_MODBUS_HOST=0.0.0.0
if not defined MACH3_MODBUS_PORT set MACH3_MODBUS_PORT=502

echo.
echo Mach3 Tablet Pendant
echo   backend=%MACH3_BACKEND%
echo   On this PC:  http://127.0.0.1:%MACH3_PORT%/
echo   On tablet:   http://%COMPUTERNAME%:%MACH3_PORT%/
echo   Mach3 talks through macropump.m1s on this PC (no Brain file).
echo   Close this window to stop the pendant server.
echo.

rem Open the UI after a short delay so the server can bind.
start "" cmd /c "ping -n 3 127.0.0.1 >nul & start http://127.0.0.1:%MACH3_PORT%/"

if exist "%~dp0.venv\Scripts\python.exe" (
  "%~dp0.venv\Scripts\python.exe" -m src.server
) else (
  where py >nul 2>&1
  if %errorlevel%==0 (
    py -3 -m src.server
  ) else (
    python -m src.server
  )
)

if errorlevel 1 (
  echo.
  echo Server exited with an error.
  echo See the traceback above. The tablet UI can start before Mach3 is polling.
  pause
)
