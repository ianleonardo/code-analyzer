@echo off
REM Start the frontend. Prints localhost and this machine's network address from ipconfig.
REM Run from anywhere: scripts\start-frontend.bat

setlocal
set "ROOT=%~dp0.."
pushd "%ROOT%"
set "ROOT=%CD%"
popd
set "PORT=7778"
REM FRONTEND_PORT in the project's .env (or the environment) changes the port. Allowed host names
REM (FRONTEND_ALLOWED_HOSTS) are read from .env by vite.config.ts.
if exist "%ROOT%\.env" (
  for /f "usebackq tokens=1,* delims==" %%A in ("%ROOT%\.env") do (
    if /i "%%A"=="FRONTEND_PORT" if not "%%B"=="" set "PORT=%%B"
  )
)
if defined FRONTEND_PORT set "PORT=%FRONTEND_PORT%"

if not exist "%ROOT%\node_modules" (
  echo Frontend dependencies not found. From the repository root, run: npm ci
  exit /b 1
)

set "LAN_IP="
for /f "tokens=2 delims=:" %%A in ('ipconfig ^| findstr /C:"IPv4"') do (
  for /f "tokens=* delims= " %%B in ("%%A") do (
    echo %%B | findstr /R "^127\. ^169\.254\." >nul
    if errorlevel 1 if not defined LAN_IP set "LAN_IP=%%B"
  )
)

cd /d "%ROOT%"
echo Frontend on this machine:    http://localhost:%PORT%
if defined LAN_IP (
  echo Frontend on the local network: http://%LAN_IP%:%PORT%
) else (
  echo Local network address: not found. ipconfig did not report a usable IPv4 address.
)
npm run dev --workspace frontend -- --host 0.0.0.0 --port %PORT%
endlocal
