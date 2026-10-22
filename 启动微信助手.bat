@echo off
setlocal
cd /d "%~dp0"
title 微信 AI 助手

if exist "backend\.venv\Scripts\python.exe" (
    "backend\.venv\Scripts\python.exe" "launcher.py" %*
) else (
    python "launcher.py" %*
)

if errorlevel 1 (
    echo.
    echo 启动失败，请查看上面的提示。
    pause
)
