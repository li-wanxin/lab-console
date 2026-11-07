#!/usr/bin/env bash
# Lab Console 构建 / 运行脚本 (macOS / Linux)
# Windows 用户请使用 打包.bat 与 启动.bat; 本脚本与它们保持同一组参数语义。
#
# 用法:
#   ./build.sh run                # 源码运行 (可带 app.py 参数, 如 --port 9000)
#   ./build.sh pack               # 打包桌面程序 (macOS 产出 .app, Linux 产出 dist/LabConsole/)
#   ./build.sh pack-onefile       # 单文件可执行 (仅 Linux 建议; macOS 窗口程序勿用 onefile)
set -euo pipefail

PY=${PY:-python3}
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

# 路径分隔符: PyInstaller --add-data 在 POSIX 上用 ':'
SEP=":"
if [ "$(uname -s)" = "Linux" ] && [ -f /proc/version ] && grep -qi microsoft /proc/version; then
  SEP=";"        # WSL 下目标仍是 Windows
fi

case "${1:-run}" in
  run)
    shift || true
    exec "$PY" app.py "$@"
    ;;
  pack)
    "$PY" -m pip install -r requirements.txt pyinstaller
    # macOS 窗口程序必须 --onedir (onefile + --windowed 在 macOS 无 app bundle
    # 且启动极慢, PyInstaller 官方不建议); 图标建议另备 .icns (见 PORTING.md)。
    "$PY" -m PyInstaller --noconfirm --clean --onedir --windowed \
      --name "LabConsole" \
      --icon "app.icns" \
      --add-data "web${SEP}web" \
      --collect-all openpyxl \
      desktop.py
    echo "完成: dist/LabConsole/"
    ;;
  pack-onefile)
    "$PY" -m pip install -r requirements.txt pyinstaller
    "$PY" -m PyInstaller --noconfirm --clean --onefile --windowed \
      --name "LabConsole" \
      --add-data "web${SEP}web" \
      --collect-all openpyxl \
      desktop.py
    echo "完成: dist/LabConsole"
    ;;
  *)
    echo "未知命令: $1 (可用: run | pack | pack-onefile)" >&2
    exit 2
    ;;
esac
