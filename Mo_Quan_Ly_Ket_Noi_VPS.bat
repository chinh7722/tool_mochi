@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Mochi - Quan ly ket noi VPS va SSH key
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" -I main_vps_connection.py
) else (
    python -I main_vps_connection.py
)
if errorlevel 1 pause
