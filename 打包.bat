@echo off
chcp 65001 >nul
cd /d "%~dp0"
title 打包 Lab Console

echo [1/3] 安装打包依赖 ...
python -m pip install --quiet pyinstaller pillow

echo [2/3] 生成安装版目录包 (onedir -> dist\LabConsole\) ...
python -m PyInstaller --noconfirm --clean --onedir --windowed ^
  --name "LabConsole" ^
  --icon app.ico ^
  --add-data "web;web" ^
  --collect-all openpyxl ^
  --collect-all libusb_package ^
  desktop.py

echo [3/3] 生成便携版单文件 (onefile) ...
python -m PyInstaller --noconfirm --onefile --windowed ^
  --name "LabConsole-Portable" ^
  --icon app.ico ^
  --add-data "web;web" ^
  --collect-all openpyxl ^
  --collect-all libusb_package ^
  desktop.py

echo.
echo 完成：
echo   安装版  dist\LabConsole\            (再用 Inno Setup 编译 setup.iss)
echo   便携版  dist\LabConsole-Portable.exe
pause
