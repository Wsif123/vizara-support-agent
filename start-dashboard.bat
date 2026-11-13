@echo off
setlocal
cd /d "%~dp0"

echo.
echo   Vizara Operator Console
echo   Local only - http://127.0.0.1:8787
echo.

set "PYEXE="
py -3.11 -c "import fastapi, uvicorn, jinja2, cryptography, multipart, chromadb" >nul 2>nul
if not errorlevel 1 set "PYEXE=py -3.11"

if not defined PYEXE (
  python -c "import fastapi, uvicorn, jinja2, cryptography, multipart, chromadb" >nul 2>nul
  if not errorlevel 1 set "PYEXE=python"
)

if not defined PYEXE (
  echo   Installing dependencies with the default Python...
  python -m pip install -r requirements.txt
  if errorlevel 1 (
    echo   Dependency install failed. See the output above.
    pause
    exit /b 1
  )
  set "PYEXE=python"
)

start "" "http://127.0.0.1:8787"
%PYEXE% -m uvicorn admin.app:app --host 127.0.0.1 --port 8787 --no-access-log
if errorlevel 1 (
  echo.
  echo   The console stopped with an error. Check the output above.
  pause
)
endlocal
