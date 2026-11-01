"""固定尺寸窗口 + 截屏，用于生成界面预览图。

用法：
    python tools/shot.py <url或文件路径> <输出png> <宽> <高> [等待秒数]

窗口按逻辑像素创建，截屏按窗口真实客户区抓取，因此高 DPI 下也不会截偏。
"""
import ctypes
import os
import sys
import time
from ctypes import wintypes

import webview
from PIL import ImageGrab

if len(sys.argv) < 5:
    print(__doc__)
    sys.exit(1)

url, out = sys.argv[1], sys.argv[2]
W, H = int(sys.argv[3]), int(sys.argv[4])
wait = float(sys.argv[5]) if len(sys.argv) > 5 else 1.4
eval_js = sys.argv[6] if len(sys.argv) > 6 else ""
TITLE = "shot"

if not url.startswith("http") and not url.startswith("file:"):
    url = "file:///" + os.path.abspath(url).replace("\\", "/")
out = os.path.abspath(out)

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    pass

user32 = ctypes.windll.user32


class RECT(ctypes.Structure):
    _fields_ = [("left", wintypes.LONG), ("top", wintypes.LONG),
                ("right", wintypes.LONG), ("bottom", wintypes.LONG)]


class POINT(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


def client_box():
    hwnd = user32.FindWindowW(None, TITLE)
    if not hwnd:
        return None
    rc = RECT()
    user32.GetClientRect(hwnd, ctypes.byref(rc))
    pt = POINT(0, 0)
    user32.ClientToScreen(hwnd, ctypes.byref(pt))
    return pt.x, pt.y, pt.x + rc.right, pt.y + rc.bottom


def shoot(window):
    time.sleep(wait)
    if eval_js:
        try:
            window.evaluate_js(eval_js)
        except Exception as exc:
            print("eval failed:", exc)
        time.sleep(0.8)
    try:
        need = window.evaluate_js(
            "Math.max(document.documentElement.scrollHeight, document.body.scrollHeight)")
        inner = window.evaluate_js("[window.innerWidth, window.innerHeight]")
        # 页面比窗口高时才撑高，避免把正常铺满的界面也拉长
        if isinstance(need, (int, float)) and isinstance(inner, list) and need > inner[1] + 6 \
                and need < 4000:
            window.resize(W, int(need) + 2)
            time.sleep(0.6)
    except Exception as exc:
        print("fit skipped:", exc)

    for _ in range(20):
        box = client_box()
        if box and box[2] - box[0] > 200:
            break
        time.sleep(0.1)
    if not box:
        print("找不到窗口，无法截屏")
        window.destroy()
        return

    # 防止窗口被意外移动导致截偏
    hwnd = user32.FindWindowW(None, TITLE)
    if hwnd:
        user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, 0x0001 | 0x0004)  # SWP_NOSIZE | SWP_NOZORDER
        time.sleep(0.15)
        box = client_box()

    img = ImageGrab.grab(box)
    img.save(out)
    print(f"saved {out}  {img.size[0]}x{img.size[1]}  box={box}")
    window.destroy()


win = webview.create_window(
    TITLE, url, width=W, height=H, x=0, y=0,
    frameless=True, on_top=True, resizable=False,
)
webview.start(shoot, win, private_mode=True, gui="edgechromium")
