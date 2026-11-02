@echo off
chcp 65001 >nul
cd /d "%~dp0"
title hoshino-tool - SSH tunnel
if not exist data mkdir data
echo Giu cua so nay mo khi quan ly VPS. Job tren VPS van chay khi dong cua so.
ssh -N -p 20052 -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 -o ServerAliveCountMax=3 -o UserKnownHostsFile=data/vps_known_hosts -L 127.0.0.1:8765:127.0.0.1:8765 root1@sv50.pcrender.net
if errorlevel 1 pause
