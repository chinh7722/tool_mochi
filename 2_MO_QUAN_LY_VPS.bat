@echo off
chcp 65001 >nul
cd /d "%~dp0control"
python -I remote_gui.py
if errorlevel 1 pause
