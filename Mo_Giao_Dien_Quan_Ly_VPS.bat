@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Mochi - Giao dien quan ly VPS
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" -I main_vps_manager.py
) else (
    python -I main_vps_manager.py
)
if errorlevel 1 pause
