"""按窗口标题 + 客户区偏移点击（窗口移动也能点准），用于自动化演示。

用法: python tools/click_window.py "Lab Console" 110 176
"""
import ctypes
import sys
import time
from ctypes import wintypes

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:  # noqa: BLE001
    pass

TITLE = sys.argv[1]
OX, OY = int(sys.argv[2]), int(sys.argv[3])

user32 = ctypes.windll.user32


class RECT(ctypes.Structure):
    _fields_ = [("left", wintypes.LONG), ("top", wintypes.LONG),
                ("right", wintypes.LONG), ("bottom", wintypes.LONG)]


class POINT(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


hwnd = user32.FindWindowW(None, TITLE)
if not hwnd:
    raise SystemExit(f"未找到窗口: {TITLE}")

user32.ShowWindow(hwnd, 9)
user32.SetForegroundWindow(hwnd)
time.sleep(0.6)

pt = POINT(0, 0)
user32.ClientToScreen(hwnd, ctypes.byref(pt))
x, y = pt.x + OX, pt.y + OY
user32.SetCursorPos(x, y)
time.sleep(0.25)
user32.mouse_event(0x0002, 0, 0, 0, 0)
time.sleep(0.08)
user32.mouse_event(0x0004, 0, 0, 0, 0)
print(f"clicked client({OX},{OY}) -> screen({x},{y})")
