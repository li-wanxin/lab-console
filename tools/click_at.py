"""向指定屏幕坐标发送一次左键点击（用于自动化验收演示）。

用法: python tools/click_at.py X Y
"""
import ctypes
import sys
import time

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:  # noqa: BLE001
    pass

x, y = int(sys.argv[1]), int(sys.argv[2])
user32 = ctypes.windll.user32
user32.SetCursorPos(x, y)
time.sleep(0.2)
user32.mouse_event(0x0002, 0, 0, 0, 0)      # LEFTDOWN
time.sleep(0.06)
user32.mouse_event(0x0004, 0, 0, 0, 0)      # LEFTUP
print(f"clicked ({x}, {y})")
