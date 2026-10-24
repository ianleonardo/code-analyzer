@echo off
REM Stop the backend and the frontend. Run from anywhere: scripts\stop.bat

setlocal
call "%~dp0stop-backend.bat"
call "%~dp0stop-frontend.bat"
endlocal
