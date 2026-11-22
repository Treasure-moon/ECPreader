@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

rem 打包成单文件 ECPReader.exe（无控制台窗口，自带 Python 运行时与前端资源）
if not exist ".venv\Scripts\python.exe" (
    echo 请先运行一次 start.bat 准备运行环境。
    pause
    exit /b 1
)

".venv\Scripts\python.exe" -c "import PyInstaller, PIL" >nul 2>nul
if errorlevel 1 (
    echo 正在安装打包工具 PyInstaller / Pillow...
    ".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -q pyinstaller pillow -i https://pypi.tuna.tsinghua.edu.cn/simple
)

echo 正在打包，大约需要 1-3 分钟...
".venv\Scripts\python.exe" -m PyInstaller --noconfirm --clean --onefile --noconsole --name ECPReader --icon "web\assets\img\logo.ico" --version-file "tools\ecp_version.txt" --add-data "web;web" --collect-all webview --collect-data pymupdf --collect-submodules pythonnet --exclude-module onnxruntime --exclude-module PIL --hidden-import clr --hidden-import webview.platforms.winforms --hidden-import uvicorn.logging --hidden-import uvicorn.loops.auto --hidden-import uvicorn.protocols.http.auto --hidden-import uvicorn.protocols.websockets.auto --hidden-import uvicorn.lifespan.on run.py
if errorlevel 1 (
    echo.
    echo [失败] 打包出错，请把上面的报错发给我。
    pause
    exit /b 1
)

echo.
echo 打包完成：dist\ECPReader.exe
echo 正在创建桌面快捷方式...
powershell -NoProfile -ExecutionPolicy Bypass -File "tools\make_shortcut.ps1"
echo.
echo 如果要生成给别人安装的安装包，继续运行 build_installer.bat
pause