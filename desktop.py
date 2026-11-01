#!/usr/bin/env python3
"""
ITECH 测试台 —— 桌面版入口
==========================
用一个原生窗口（Windows WebView2）承载界面，双击即开，不经过浏览器。

    python desktop.py          # 直接从源码运行
    ITECH测试台.exe            # 打包后的成品

界面与后端在本机回环地址通信（127.0.0.1，端口随机），外部无法访问。
"""

from __future__ import annotations

import sys
import threading
import time
import traceback
import webbrowser
from pathlib import Path

import app as backend

APP_TITLE = "ITECH 测试台 · IT6302 + IT8511A+"


class Api:
    """暴露给前端的原生能力（另存为对话框等）。"""

    def __init__(self) -> None:
        self._window = None
        self.wv = None
        self.last_dir = str(Path.home() / "Documents")

    # ---------- 导出 ----------
    def save_csv(self, role: str = "power") -> dict:
        try:
            with backend.BENCH.lock:
                name, text = backend.BENCH.export(role)
            if not text:
                return {"ok": False, "error": "暂无数据可导出"}
            if self._window is None or self.wv is None:
                return {"ok": False, "error": "当前环境不支持另存为对话框"}
            target = self._window.create_file_dialog(
                self.wv.SAVE_DIALOG,
                directory=self.last_dir,
                save_filename=name,
                file_types=("CSV 文件 (*.csv)",),
            )
            if not target:
                return {"ok": False, "cancelled": True}
            path = Path(target if isinstance(target, str) else target[0])
            if path.suffix.lower() != ".csv":
                path = path.with_suffix(".csv")
            # 用 bytes 写入：csv 生成的是 \r\n，文本模式会在 Windows 上二次转换出空行
            path.write_bytes(text.encode("utf-8-sig"))
            self.last_dir = str(path.parent)
            backend.BENCH.log("ok", f"已导出 {path.name}（{path.stat().st_size} 字节）")
            return {"ok": True, "path": str(path)}
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            return {"ok": False, "error": str(exc)}

    def pick_save_path(self, default_name: str = "") -> dict:
        """记录功能：弹出另存为对话框，只返回所选路径，不写文件。"""
        try:
            if self._window is None or self.wv is None:
                return {"ok": False, "error": "当前环境不支持另存为对话框"}
            target = self._window.create_file_dialog(
                self.wv.SAVE_DIALOG,
                directory=self.last_dir,
                save_filename=default_name or "记录.csv",
                file_types=("CSV 文件 (*.csv)",),
            )
            if not target:
                return {"cancelled": True}
            path = Path(target if isinstance(target, str) else target[0])
            if path.suffix.lower() != ".csv":
                path = path.with_suffix(".csv")
            return {"ok": True, "path": str(path)}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}

    def pick_dir(self) -> dict:
        """记录功能：弹出系统目录选择对话框，返回所选目录路径。"""
        try:
            if self._window is None or self.wv is None:
                return {"ok": False, "error": "当前环境不支持目录选择"}
            target = self._window.create_file_dialog(
                self.wv.FOLDER_DIALOG,
                directory=self.last_dir,
            )
            if not target:
                return {"cancelled": True}
            d = target if isinstance(target, str) else target[0]
            return {"ok": True, "path": str(d)}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}

    def reveal(self, path: str) -> dict:
        """在资源管理器中定位文件。"""
        try:
            import subprocess
            subprocess.Popen(["explorer", "/select,", path])
            return {"ok": True}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}


def _window_size() -> tuple[int, int]:
    """按屏幕分辨率给一个合适的初始窗口尺寸。"""
    try:
        import ctypes
        user32 = ctypes.windll.user32
        sw, sh = user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
    except Exception:  # noqa: BLE001
        sw, sh = 1920, 1080
    w = max(1120, min(1580, int(sw * 0.86)))
    h = max(700, min(1000, int(sh * 0.88)))
    return w, h


def _start_server() -> tuple[threading.Thread, str]:
    srv = backend.build_server("127.0.0.1", 0)
    port = srv.server_address[1]
    t = threading.Thread(target=srv.serve_forever, daemon=True, name="http")
    t.start()
    return t, f"http://127.0.0.1:{port}/"


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass

    _thread, url = _start_server()

    try:
        import webview
    except ImportError:
        print("未安装 pywebview，改用浏览器模式…")
        webbrowser.open(url)
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            backend.shutdown()
        return

    api = Api()
    api.wv = webview

    win_w, win_h = _window_size()
    window = webview.create_window(
        APP_TITLE,
        url,
        js_api=api,
        width=win_w,
        height=win_h,
        min_size=(1120, 700),
        background_color="#0a0e13",
    )
    api._window = window

    print(f"桌面窗口已启动: {url}")

    try:
        webview.start(debug=False, private_mode=False)
    except Exception as exc:  # noqa: BLE001
        # WebView2 运行时缺失等情况下退回浏览器，保证软件仍可用
        print(f"桌面窗口启动失败({exc})，改用浏览器打开…")
        webbrowser.open(url)
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
    finally:
        backend.shutdown()
        print("已退出，仪器输出已关闭")


if __name__ == "__main__":
    main()
