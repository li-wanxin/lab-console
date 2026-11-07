"""跨平台支撑层
================
把所有随操作系统变化的差异点 (数据目录 / 用系统程序打开文件 / 根目录枚举 /
平台判断) 收敛到这一个模块, 其余代码只调用这里 —— Mac/Linux 移植时
仅需扩展本模块, 不必再全库搜索 os.startfile / APPDATA。

> 移植新平台时: 在对应分支补齐实现即可; 新增差异点一律放这里, 不要散落。
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

IS_WINDOWS = sys.platform.startswith("win")
IS_MACOS = sys.platform == "darwin"
APP_NAME = "LabConsole"


def app_data_dir(app: str = APP_NAME) -> Path:
    """用户级数据目录 (更新缓存等), 不存在则创建。

    Windows: %APPDATA%/LabConsole
    macOS:   ~/Library/Application Support/LabConsole
    Linux:   $XDG_DATA_HOME/LabConsole (缺省 ~/.local/share/LabConsole)
    """
    if IS_WINDOWS:
        base = os.environ.get("APPDATA") or str(Path.home())
        p = Path(base) / app
    elif IS_MACOS:
        p = Path.home() / "Library" / "Application Support" / app
    else:
        base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
        p = Path(base) / app
    try:
        p.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return p


def open_external(target) -> None:
    """用系统默认程序打开文件或 URL (跨平台 os.startfile 替代)。"""
    t = str(target)
    if IS_WINDOWS:
        os.startfile(t)  # noqa: S606  Windows 壳默认打开方式
    elif IS_MACOS:
        subprocess.Popen(["open", t])  # noqa: S603
    else:
        subprocess.Popen(["xdg-open", t])  # noqa: S603


def list_roots() -> list[str]:
    """目录选择器的根级位置: Windows 为存在的盘符, POSIX 为 /。"""
    if IS_WINDOWS:
        return [f"{c}:\\" for c in "CDEFGHIJKLMNOPQRSTUVWXYZ"
                if Path(f"{c}:\\").exists()]
    return ["/"]


def documents_dir() -> Path:
    """记录文件默认落盘位置 (存在 Documents 则用之)。"""
    doc = Path.home() / "Documents"
    return doc if doc.is_dir() else Path.home()
