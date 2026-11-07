@echo off
chcp 65001 >nul
cd /d "%~dp0"
title 打包 Lab Console

echo [1/2] 安装打包依赖 ...
python -m pip install --quiet pyinstaller pillow

echo [2/2] 生成单文件程序 ...
python -m PyInstaller --noconfirm --clean --onefile --windowed ^
  --name "LabConsole-Portable" ^
  --icon app.ico ^
  --add-data "web;web" ^
  desktop.py

echo.
echo 完成：dist\LabConsole-Portable.exe
pause
