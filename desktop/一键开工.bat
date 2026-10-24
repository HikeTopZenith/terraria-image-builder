@echo off
setlocal
title TerrariaImageBuilder Launcher

set "TML_DIR=D:\Steam\steamapps\common\tModLoader"
set "UIPY=D:\deepseek\ComfyUI\.venv\Scripts\pythonw.exe"
set "UIAPP=%~dp0ui\app.py"

echo [1/2] Starting tModLoader ...
if exist "%TML_DIR%\start-tModLoader.bat" (
    start "" /D "%TML_DIR%" "%TML_DIR%\start-tModLoader.bat"
) else (
    echo     [!] start-tModLoader.bat not found, fallback to Steam
    start "" "steam://rungameid/1281930"
)

echo [2/2] Starting builder UI ...
if exist "%UIPY%" (
    start "" "%UIPY%" "%UIAPP%"
) else (
    echo     [!] pythonw not found: %UIPY%
    echo     Please check the ComfyUI venv
)

echo.
echo Done. Game takes ~30s to load; UI starts first.
timeout /t 3 >nul
endlocal
