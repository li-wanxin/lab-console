"""把 Lab Console 窗口移到前台并调整到指定位置/尺寸。调试用。"""
import ctypes
import sys

user32 = ctypes.windll.user32
TITLE = "Lab Console"
x, y, w, h = (int(v) for v in sys.argv[1:5]) if len(sys.argv) > 4 else (40, 40, 1440, 900)

hwnd = user32.FindWindowW(None, TITLE)
if not hwnd:
    print("未找到窗口")
    sys.exit(1)

if user32.IsIconic(hwnd):
    user32.ShowWindow(hwnd, 9)          # SW_RESTORE
user32.MoveWindow(hwnd, x, y, w, h, True)
user32.ShowWindow(hwnd, 5)              # SW_SHOW
user32.SetForegroundWindow(hwnd)
print(f"窗口 {hwnd} 已置于 ({x},{y}) {w}x{h} 并置顶")
