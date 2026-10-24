@echo off
chcp 65001 >nul
setlocal
set "PY=D:\deepseek\ComfyUI\.venv\Scripts\pythonw.exe"
if not exist "%PY%" set "PY=D:\deepseek\ComfyUI\.venv\Scripts\python.exe"
if not exist "%PY%" (
  echo [ERROR] 找不到带 numpy/Pillow 的 Python，请修改本脚本里的 PY 变量
  pause
  exit /b 1
)
start "" "%PY%" "%~dp0ui\app.py"
endlocal
