@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Lab Console

rem 优先用固定解释器路径, 找不到再退回 PATH
set "PYW=C:\Users\liwanxin\.workbuddy\binaries\python\versions\3.14.3\pythonw.exe"
set "PY=C:\Users\liwanxin\.workbuddy\binaries\python\versions\3.14.3\python.exe"
if not exist "%PY%" set "PY=python"
if not exist "%PYW%" set "PYW=pythonw"

rem 首次运行自动补依赖
"%PY%" -c "import webview" >nul 2>nul
if errorlevel 1 (
  echo 正在安装界面依赖 pywebview ...
  "%PY%" -m pip install --quiet pywebview
)
"%PY%" -c "import serial" >nul 2>nul
if errorlevel 1 (
  echo 正在安装串口依赖 pyserial ...
  "%PY%" -m pip install --quiet pyserial
)
"%PY%" -c "import usb.core, libusb_package" >nul 2>nul
if errorlevel 1 (
  echo 正在安装 USB 示波器依赖 pyusb / libusb-package ...
  "%PY%" -m pip install --quiet pyusb libusb-package
)

start "" "%PYW%" desktop.py
exit
