@echo off
setlocal

cd /d "%~dp0"

set "NPM_CMD=npm"
if exist "%ProgramFiles%\nodejs\npm.cmd" set "NPM_CMD=%ProgramFiles%\nodejs\npm.cmd"

if not exist "web\node_modules" (
  pushd web
  call "%NPM_CMD%" install
  if errorlevel 1 goto :error
  popd
)

pushd web
call "%NPM_CMD%" run build
if errorlevel 1 goto :error
popd

echo.
echo Dolev AI dashboard: http://127.0.0.1:8000
echo Use the Start Agent button in the dashboard to begin scraping.
echo.
py -3.12 scripts\serve_ui.py
goto :eof

:error
echo.
echo Failed to start Dolev AI dashboard.
pause
exit /b 1
