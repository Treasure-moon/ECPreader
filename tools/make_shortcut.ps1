# 在桌面创建 ECPreader 快捷方式（图标使用 exe 内嵌的 logo）
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$exe = Join-Path $root 'dist\ECPReader.exe'
if (-not (Test-Path $exe)) {
    throw "未找到 $exe，请先运行 build_exe.bat 打包。"
}
$desktop = [Environment]::GetFolderPath('Desktop')
$link = Join-Path $desktop 'ECPreader.lnk'
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($link)
$shortcut.TargetPath = $exe
$shortcut.WorkingDirectory = Split-Path $exe
$shortcut.IconLocation = "$exe,0"
$shortcut.Description = 'ECPreader · 论文双语对照阅读器'
$shortcut.Save()
Write-Host "快捷方式已创建：$link"
