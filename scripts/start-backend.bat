@echo off
REM Start the backend at http://localhost:8001. Run from anywhere: scripts\start-backend.bat

setlocal
set "ROOT=%~dp0.."
pushd "%ROOT%"
set "ROOT=%CD%"
popd

if not exist "%ROOT%\backend\.venv\Scripts\uvicorn.exe" (
  echo Backend virtual environment not found at backend\.venv
  echo Create it with:
  echo   cd backend
  echo   py -3 -m venv .venv
  echo   .venv\Scripts\pip install -r requirements.txt
  exit /b 1
)

cd /d "%ROOT%\backend"
echo Backend: http://localhost:8001
.venv\Scripts\uvicorn.exe app.main:app --reload --port 8001
endlocal
