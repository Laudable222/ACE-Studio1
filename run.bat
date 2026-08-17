@echo off
REM ============================================================================
REM  ACE Studio v2 — one-click launcher (Windows)
REM  Starts the FastAPI backend and the React (Vite) frontend, then opens the app.
REM ============================================================================
setlocal
cd /d "%~dp0"

echo(
echo   ACE Studio v2
echo   -------------

REM ---- Automatic GitHub updates are intentionally disabled. ----
REM ---- Python backend: venv + deps ----------------------------------------
if not exist "backend\.venv\Scripts\python.exe" (
  echo [setup] creating Python virtual environment...
  python -m venv "backend\.venv" || goto :err
)
set "PY=backend\.venv\Scripts\python.exe"
set "DEPS_STAMP=backend\.venv\.ace_requirements_stamp"
if not exist "%DEPS_STAMP%" goto :install_deps
for /f %%A in ('certutil -hashfile backend\requirements.txt SHA256 ^| findstr /v "CertUtil"') do set "REQ_HASH=%%A"
set /p OLD_HASH=<"%DEPS_STAMP%"
if /i not "%REQ_HASH%"=="%OLD_HASH%" goto :install_deps
goto :deps_done
:install_deps
echo [setup] installing backend dependencies...
"%PY%" -m pip install --quiet --upgrade pip
"%PY%" -m pip install --quiet -r backend\requirements.txt || goto :err
for /f %%A in ('certutil -hashfile backend\requirements.txt SHA256 ^| findstr /v "CertUtil"') do set "REQ_HASH=%%A"
>"%DEPS_STAMP%" echo %REQ_HASH%
:deps_done

REM ---- Frontend deps ------------------------------------------------------
if not exist "frontend\node_modules" (
  echo [setup] installing frontend dependencies ^(first run only^)...
  pushd frontend
  call npm install || (popd & goto :err)
  popd
)

REM ---- Launch: backend FIRST, then wait until it answers, then frontend -----
echo [run] starting backend on http://127.0.0.1:8766
start "ACE backend" cmd /c ""%PY%" -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8766"

echo [run] waiting for the backend to come up...
powershell -NoProfile -Command "$n=0; while($n -lt 90){ try{ (Invoke-WebRequest -UseBasicParsing 'http://127.0.0.1:8766/api' -TimeoutSec 2) | Out-Null; exit 0 }catch{ Start-Sleep -Milliseconds 800; $n++ } }; exit 1"
if errorlevel 1 (
  echo [warn] backend did not respond in time. Check the "ACE backend" window for errors.
) else (
  echo [run] backend is up.
)

echo [run] starting frontend on http://localhost:5173
pushd frontend
start "ACE frontend" cmd /c "npm run dev"
popd

REM give Vite a moment, then open the browser
timeout /t 4 /nobreak >nul
start "" "http://localhost:5173"

echo(
echo   ACE Studio is starting. Two windows opened (backend + frontend).
echo   Open http://localhost:5173 if the browser did not.
echo   Close those two windows to stop the app.
echo(
goto :eof

:err
echo(
echo [error] setup failed. Make sure Python 3.11+ and Node.js are installed and on PATH.
pause
exit /b 1
