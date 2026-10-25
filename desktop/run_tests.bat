@echo off
chcp 65001 >nul
setlocal
rem ===================================================================
rem  Terraria Image Builder - 一键跑全部测试
rem  用法：双击本文件即可。不需要记 PYTHONPATH / TEMP 这些环境变量。
rem ===================================================================

set "HERE=%~dp0"
cd /d "%HERE%"

rem 找一个带 numpy / Pillow / aiohttp 的 Python
set "PY="
if exist "D:\deepseek\ComfyUI\.venv\Scripts\python.exe" set "PY=D:\deepseek\ComfyUI\.venv\Scripts\python.exe"
if not defined PY if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if not defined PY (
  echo [ERROR] 找不到 Python，请把 PY 变量改成你自己的解释器路径
  pause
  exit /b 1
)
echo 使用解释器: %PY%
echo.

rem 测试要能 import engine.* （PYTHONPATH）
set "PYTHONPATH=%HERE%"
set "PYTHONIOENCODING=utf-8"
rem 本机 TEMP 有时不可用，测试里会建临时文件，强制指到固定目录
if not exist "D:\deepseek\_tmp" mkdir "D:\deepseek\_tmp" >nul 2>&1
set "TEMP=D:\deepseek\_tmp"
set "TMP=D:\deepseek\_tmp"

set FAIL=0
for %%T in (test_engine test_matcher_v2 test_colorimetry test_place_command test_pipeline_v2 test_checksum_golden) do (
  echo ----------------------------------------------------------------
  echo  %%T
  echo ----------------------------------------------------------------
  "%PY%" "tests\%%T.py"
  if errorlevel 1 set FAIL=1
)

echo.
echo ==================================================================
if "%FAIL%"=="0" (
  echo  全部测试通过
) else (
  echo  有测试失败，请把上面的输出发出来
)
echo ==================================================================
pause