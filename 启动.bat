@echo off
chcp 65001 >nul
cd /d "%~dp0"
title ITECH 测试台

rem 首次运行自动补依赖
python -c "import webview" >nul 2>nul
if errorlevel 1 (
  echo 正在安装界面依赖 pywebview ...
  python -m pip install --quiet pywebview
)
python -c "import serial" >nul 2>nul
if errorlevel 1 (
  echo 正在安装串口依赖 pyserial ...
  python -m pip install --quiet pyserial
)

start "" pythonw.exe desktop.py
exit
