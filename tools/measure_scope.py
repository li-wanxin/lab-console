"""打开页面后打印示波器画布相关元素的实际尺寸 (调试用)。"""
import json
import sys
import time

import webview

url = sys.argv[1]
eval_js = sys.argv[2] if len(sys.argv) > 2 else "setView('d3')"
JS = """
(function () {
  const q = s => document.querySelector(s);
  const r = el => el ? { w: Math.round(el.getBoundingClientRect().width),
                         h: Math.round(el.getBoundingClientRect().height),
                         x: Math.round(el.getBoundingClientRect().left),
                         y: Math.round(el.getBoundingClientRect().top) } : null;
  const cv = q('.scope-canvas');
  return JSON.stringify({
    dpr: window.devicePixelRatio,
    inner: [window.innerWidth, window.innerHeight],
    panel: r(q('.vpanel')),
    wave: r(q('.scope-wave')),
    screen: r(q('.scope-screen')),
    canvas: r(cv),
    attr: cv ? [cv.width, cv.height] : null,
    styleW: cv ? cv.style.width : null,
    scrollH: document.documentElement.scrollHeight,
  });
})()
"""


def run(win):
    time.sleep(3.0)
    try:
        win.evaluate_js(eval_js)
    except Exception as exc:
        print("eval failed:", exc)
    time.sleep(2.5)
    try:
        out = win.evaluate_js(JS)
        print(out)
    except Exception as exc:
        print("measure failed:", exc)
    win.destroy()


w = webview.create_window("measure", url, width=1560, height=980, x=0, y=0,
                          frameless=True, on_top=True, resizable=False)
webview.start(run, w, private_mode=True, gui="edgechromium")
