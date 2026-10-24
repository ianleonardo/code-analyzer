@echo off
REM Start the backend and the frontend, each in its own window.
REM Run from anywhere: scripts\start.bat

setlocal
set "ROOT=%~dp0.."
pushd "%ROOT%"
set "ROOT=%CD%"
popd

start "Code Analyzer backend" "%ROOT%\scripts\start-backend.bat"
start "Code Analyzer frontend" "%ROOT%\scripts\start-frontend.bat"
endlocal
