@echo off
REM ============================================================================
REM  package_for_sharing.bat  —  build a clean, shareable ACE Studio v2 zip.
REM
REM  KEEPS   : launchers and application source (backend\, frontend\, docs\).
REM            Automatic GitHub updates are intentionally disabled.
REM  DROPS   : anything personal or rebuildable —
REM              data\            your database, licence, caches, saved prompts
REM              session.pkl      your authenticated BRAIN session
REM              ace.log          run logs (may contain expressions / alpha ids)
REM              .env             any local secrets/overrides
REM              support_token.txt
REM              .claude          your local Claude Code config + memory (never shared)
REM              secrets          any secrets folder that happens to sit in the tree
REM              backend\.venv, frontend\node_modules, frontend\dist, __pycache__, .git
REM
REM  The recipient unzips, opens the "ACE Studio" folder, and double-clicks run.bat.
REM  It installs dependencies and launches. They log in with
REM  THEIR own WorldQuant BRAIN account (Settings tab) and set THEIR own LLM keys, so
REM  nothing of yours ships.
REM ============================================================================
setlocal
cd /d "%~dp0"

set "OUTDIR=%USERPROFILE%\Desktop\ACE Studio"
set "OUTZIP=%USERPROFILE%\Desktop\ACE Studio.zip"

echo(
echo   Building a shareable copy of ACE Studio v2...
echo(

if exist "%OUTDIR%" rmdir /s /q "%OUTDIR%"
if exist "%OUTZIP%" del /q "%OUTZIP%"

REM robocopy mirrors the project, skipping private state and rebuildable folders.
REM   /E   include subdirectories (even empty)
REM   /XD  exclude directories   /XF exclude files
REM Excluding "%CD%\data" by full path drops only the top-level data\ folder.
robocopy "." "%OUTDIR%" /E ^
  /XD ".git" ".claude" "secrets" ".venv" "node_modules" "__pycache__" ".ipynb_checkpoints" "dist" "%CD%\data" ^
  /XF "session.pkl" "ace.log" "support_token.txt" ".env" "*.pyc" >nul
if errorlevel 8 (
  echo [error] robocopy failed while copying files. Nothing was zipped.
  pause
  exit /b 1
)

REM Safety net: never let a stray copy of local state slip into the package.
if exist "%OUTDIR%\data"        rmdir /s /q "%OUTDIR%\data"
if exist "%OUTDIR%\session.pkl" del /q "%OUTDIR%\session.pkl"
if exist "%OUTDIR%\ace.log"     del /q "%OUTDIR%\ace.log"

echo   Compressing...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "Compress-Archive -LiteralPath '%OUTDIR%' -DestinationPath '%OUTZIP%' -Force"
if errorlevel 1 (
  echo [error] could not create the zip.
  pause
  exit /b 1
)

echo(
echo ============================================================
echo   Done. Share this single file with anyone:
echo(
echo      %OUTZIP%
echo(
echo   They unzip it, open the "ACE Studio" folder, and
echo   double-click run.bat. Automatic GitHub updates are disabled.
echo   This build will not overwrite your local enhancements.
echo(
echo ============================================================
echo(
pause
