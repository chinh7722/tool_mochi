@echo off
chcp 65001 >nul
cd /d "%~dp0"
title hoshino-tool - Web local quan ly VPS
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" -I main_mochi_web.py %*
) else (
    python -I main_mochi_web.py %*
)
if errorlevel 1 pause
