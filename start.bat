@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
title ECP Reader

rem 优先用项目自带环境（启动快、数据都在 data 目录）。
rem 启动逻辑放在 app\launcher.py，因此 run.py 被误清空也不影响启动。
set "PY=.venv\Scripts\python.exe"
set "PYW=.venv\Scripts\pythonw.exe"

if not exist "%PY%" goto :bootstrap

rem ---------- 预检：依赖与后端导入是否正常 ----------
"%PY%" -m app.launcher --check >nul 2>&1
if errorlevel 1 goto :fallback_exe

:launch
rem pythonw：不出现黑色命令行窗口，只弹应用窗口
start "" "%PYW%" -m app.launcher %*
exit /b 0

:fallback_exe
echo [提示] Python 环境不完整，尝试改用打包版 ECPReader.exe...
if exist "%~dp0dist\ECPReader.exe" (
    set "ECP_DATA_DIR=%~dp0data"
    start "" "%~dp0dist\ECPReader.exe" %*
    exit /b 0
)
goto :install_deps

:bootstrap
set "PYEXE="
if exist ".venv\Scripts\python.exe" set "PYEXE=.venv\Scripts\python.exe"
if not defined PYEXE (
    where python >nul 2>nul && set "PYEXE=python"
)
if not defined PYEXE (
    where py >nul 2>nul && set "PYEXE=py -3"
)
if not defined PYEXE (
    echo [错误] 没有检测到 Python，也没有找到已打包的 ECPReader.exe。
    echo 请安装 Python 3.10+：https://www.python.org/downloads/
    pause
    exit /b 1
)

echo 首次运行，正在创建独立运行环境（不会影响系统 Python）...
%PYEXE% -m venv .venv
if errorlevel 1 (
    echo [错误] 创建虚拟环境失败。
    pause
    exit /b 1
)
goto :install_deps

:install_deps
echo 正在安装/补齐依赖（使用清华镜像，约 1-3 分钟）...
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -q -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
if errorlevel 1 (
    echo [警告] 镜像安装失败，尝试官方源...
    ".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -q -r requirements.txt
)

".venv\Scripts\python.exe" -m app.launcher --check >nul 2>&1
if errorlevel 1 (
    echo.
    echo [错误] 依赖安装后自检仍未通过。
    echo 请把窗口里的报错发给我，或查看 data\app.log。
    pause
    exit /b 1
)
goto :launch