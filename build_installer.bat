@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

set VERSION=1.4.0

if not exist ".venv\Scripts\python.exe" (
    echo 请先运行一次 start.bat 准备运行环境。
    pause
    exit /b 1
)

if not exist "dist\ECPReader.exe" (
    echo 还没有 dist\ECPReader.exe，请先运行 build_exe.bat。
    pause
    exit /b 1
)

echo 正在检查安装程序代码...
".venv\Scripts\python.exe" -m py_compile "installer\setup_app.py" "installer\uninstall_app.py"
if errorlevel 1 (
    echo [失败] 安装程序代码有语法错误。
    pause
    exit /b 1
)

echo 正在打包卸载程序...
if exist "installer\build\ECPreaderUninstall.exe" del /q "installer\build\ECPreaderUninstall.exe"
".venv\Scripts\python.exe" -m PyInstaller --noconfirm --clean --onefile --noconsole --name ECPreaderUninstall --icon "%~dp0web\assets\img\logo.ico" --distpath "installer\build" --workpath "installer\build\work-uninstall" --specpath "installer\build" "installer\uninstall_app.py"
if errorlevel 1 (
    echo [失败] 卸载程序打包出错。
    pause
    exit /b 1
)

echo 正在打包安装程序（约 1-2 分钟）...
".venv\Scripts\python.exe" -m PyInstaller --noconfirm --clean --onefile --noconsole --name ECPreader-Setup-%VERSION% --icon "%~dp0web\assets\img\logo.ico" --add-data "%~dp0dist\ECPReader.exe;payload" --add-data "%~dp0installer\build\ECPreaderUninstall.exe;payload" --add-data "%~dp0web\assets\img\logo.ico;assets" --add-data "%~dp0web\assets\img\logo-64.png;assets" --add-data "%~dp0web\assets\img\logo-256.png;assets" --distpath "installer\dist" --workpath "installer\build\work-setup" --specpath "installer\build" "installer\setup_app.py"
if errorlevel 1 (
    echo [失败] 安装程序打包出错。
    pause
    exit /b 1
)

echo.
echo 安装包已生成：installer\dist\ECPreader-Setup-%VERSION%.exe
echo.
pause