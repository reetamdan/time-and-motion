@echo off
rem Start Aegis Ops on Windows: production dashboard at http://127.0.0.1:8000/ (-Dev for the Vite dev UI).
rem On an RTX A6000 the configs\a6000 profile is selected automatically; see docs\rtx-a6000.md.
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\a6000\start.ps1" %*
if errorlevel 1 pause
