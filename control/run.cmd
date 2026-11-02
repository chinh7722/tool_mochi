@echo off
set "QT_LOGGING_RULES=qt.qpa.*=false;qt.qpa.window=false"
set "PYTHONWARNINGS=ignore"
title MiraiMind Local Control & Automation Tool
chcp 65001 >nul
cd /d "%~dp0"

where python >nul 2>&1
if %ERRORLEVEL% equ 0 (
    start "" python -I main.py %*
    exit /b 0
)

echo [LOI] Khong tim thay Python!
pause
exit /b 1
