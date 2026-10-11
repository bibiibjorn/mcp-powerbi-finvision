@echo off
:: Double-click this file to install the PBI + Fabric skills.
:: It runs install.ps1 with the execution-policy bypass so Windows doesn't block it.
setlocal
cd /d "%~dp0"

echo.
echo Installing Power BI + Fabric skills into your ~/.claude folder...
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1"

echo.
echo ----------------------------------------------------------------
echo Done. Restart Claude Code so the new skills load.
echo ----------------------------------------------------------------
echo.
pause
