@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
title ECP Reader - 调试控制台

rem 调试用：保留控制台并输出日志，用浏览器打开界面。
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" -m app.launcher --browser %*
) else (
    python -m app.launcher --browser %*
)
pause
