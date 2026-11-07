# -*- coding: utf-8 -*-
"""渲染选定图标 (A 示波器) 为多尺寸 app.ico。

与 tools/gen_icon_previews.py 的 A 方案同一套绘制逻辑, 但全部坐标按
比例缩放: 4x 超采样渲染 1024px, 再 LANCZOS 降采样出各尺寸, 保证
16px 小图也平滑。同时输出 web/favicon.png 供浏览器标签页使用。
"""
import math
import os

from PIL import Image, ImageDraw

K = 4                       # 超采样倍率 (256 * 4 = 1024)
S = 256 * K


def canvas():
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    return img, ImageDraw.Draw(img)


def px(*vals):
    """坐标按 K 缩放; 支持单值或多个值。"""
    vals = list(vals)
    for i, v in enumerate(vals):
        if isinstance(v, (int, float)):
            vals[i] = int(round(v * K))
    return vals[0] if len(vals) == 1 else vals


def main():
    img, d = canvas()
    # 机身
    d.rounded_rectangle(px(8, 8, 248, 248), radius=px(56),
                        fill=(30, 37, 48, 255), outline=(86, 102, 118, 255),
                        width=px(3))
    d.arc(px(20, 16, 236, 60), start=200, end=340,
          fill=(255, 255, 255, 28), width=px(2))

    # 屏幕
    d.rounded_rectangle(px(30, 30, 226, 152), radius=px(12),
                        fill=(9, 26, 32, 255), outline=(64, 84, 96, 255),
                        width=px(3))
    for x in range(54, 226, 24):
        d.line(px(x, 36, x, 146), fill=(22, 58, 66, 255), width=px(1))
    for y in range(60, 146, 24):
        d.line(px(36, y, 220, y), fill=(22, 58, 66, 255), width=px(1))

    # 方波
    wy, amp = 94, 30
    pts = [px(40, wy)]
    x = 40
    while x < 216:
        pts += [px(x, wy - amp), px(x + 22, wy - amp),
                px(x + 22, wy + amp), px(x + 44, wy + amp)]
        x += 44
    d.line(pts, fill=(79, 209, 224, 255), width=px(6), joint="curve")

    # 旋钮 + 电源灯
    for cx, ind in ((74, -50), (182, 40)):
        cx = px(cx)
        cy = px(198)
        r = px(20)
        d.ellipse([cx - r, cy - r, cx + r, cy + r],
                  fill=(44, 55, 70, 255), outline=(110, 128, 146, 255),
                  width=px(3))
        a = math.radians(ind)
        d.line([cx, cy, cx + px(13) * math.cos(a), cy + px(13) * math.sin(a)],
               fill=(180, 200, 214, 255), width=px(4))
    d.ellipse(px(121, 191, 135, 205), fill=(53, 224, 138, 255))

    # 输出: 各尺寸 ico + favicon
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sizes = [(256, 256), (128, 128), (64, 64), (48, 48),
             (32, 32), (24, 24), (16, 16)]
    img.save(os.path.join(root, "app.ico"), format="ICO", sizes=sizes)
    web = os.path.join(root, "web")
    img.resize((32, 32), Image.LANCZOS).save(os.path.join(web, "favicon.png"))
    prev = os.path.join(web, "..", "tools", "icon_candidates", "A_最终渲染.png")
    img.resize((256, 256), Image.LANCZOS).save(prev)
    print("app.ico + web/favicon.png written")


if __name__ == "__main__":
    main()
