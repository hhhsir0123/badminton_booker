@echo off
chcp 65001 >nul
cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" (
  set "PY=.venv\Scripts\python.exe"
) else (
  set "PY=python"
)

echo 正在启动控制台，浏览器会自动打开 http://127.0.0.1:8765/
echo 请在网页点击“关闭程序”退出。
echo.
"%PY%" main.py --web
if errorlevel 1 pause
