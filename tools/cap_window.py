"""截取已存在窗口的客户区（按标题查找），用于验收运行中的桌面程序。

用法: python tools/cap_window.py "Lab Console" out.png
"""
import ctypes
import sys
import time
from ctypes import wintypes

from PIL import ImageGrab

TITLE = sys.argv[1] if len(sys.argv) > 1 else "Lab Console"
OUT = sys.argv[2] if len(sys.argv) > 2 else "tools/window.png"

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:  # noqa: BLE001
    pass

user32 = ctypes.windll.user32


class RECT(ctypes.Structure):
    _fields_ = [("left", wintypes.LONG), ("top", wintypes.LONG),
                ("right", wintypes.LONG), ("bottom", wintypes.LONG)]


class POINT(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


hwnd = 0
for _ in range(40):
    hwnd = user32.FindWindowW(None, TITLE)
    if hwnd and user32.IsWindowVisible(hwnd):
        break
    time.sleep(0.2)

if not hwnd:
    raise SystemExit(f"未找到窗口: {TITLE}")

user32.ShowWindow(hwnd, 9)                    # SW_RESTORE
user32.SetForegroundWindow(hwnd)
time.sleep(0.9)

rc = RECT()
user32.GetClientRect(hwnd, ctypes.byref(rc))
pt = POINT(0, 0)
user32.ClientToScreen(hwnd, ctypes.byref(pt))
box = (pt.x, pt.y, pt.x + rc.right, pt.y + rc.bottom)
img = ImageGrab.grab(box)
img.save(OUT)
print(f"saved {OUT} {img.size[0]}x{img.size[1]} box={box}")
