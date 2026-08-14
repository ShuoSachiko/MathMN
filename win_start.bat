@echo off
chcp 65001 >nul
set SCRIPT_DIR=%~dp0
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%scripts\start-local.ps1"
if errorlevel 1 (
  echo.
  echo 启动失败。首次使用请先运行：
  echo powershell -ExecutionPolicy Bypass -File scripts\setup-local.ps1
  pause
  exit /b 1
)
echo.
echo MathModelAgent 已在后台启动，日志位于 .runtime\logs。
pause
