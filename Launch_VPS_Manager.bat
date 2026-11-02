@echo off
chcp 65001 >nul
cd /d "%~dp0"
title hoshino-tool - Quan ly VPS
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" -I main_mochi_gui.py --remote
) else (
    python -I main_mochi_gui.py --remote
)
if errorlevel 1 pause
