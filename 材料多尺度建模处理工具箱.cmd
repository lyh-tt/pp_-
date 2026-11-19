@echo off
setlocal
cd /d "%~dp0"
set "UV_PROJECT_ENVIRONMENT=.venv-win"
if exist ".venv-win\Scripts\pp-rve-rpt-texgen.exe" goto launch
if exist "%USERPROFILE%\.local\bin\uv.exe" (
    "%USERPROFILE%\.local\bin\uv.exe" sync --locked --no-dev
) else (
    uv sync --locked --no-dev
)
if errorlevel 1 (
    pause
    exit /b 1
)

:launch
start "" ".venv-win\Scripts\pp-rve-rpt-texgen.exe"
