# -*- coding: utf-8 -*-
"""为 Lab Console 生成 4 个候选应用图标 (电子仪器风格), 256x256 PNG 预览。

选定后用同文件放大渲染并输出多尺寸 .ico, 保证预览与成品一致。
"""
import math
import os
import sys

from PIL import Image, ImageDraw, ImageFont

OUT = os.path.join(os.path.dirname(__file__), "icon_candidates")
os.makedirs(OUT, exist_ok=True)
S = 256


def canvas():
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    return img, ImageDraw.Draw(img)


def body(d, fill, border, radius=56):
    d.rounded_rectangle([8, 8, S - 8, S - 8], radius=radius, fill=fill,
                        outline=border, width=3)
    # 顶部高光弧线, 增加金属质感
    d.arc([20, 16, S - 20, 60], start=200, end=340,
          fill=(255, 255, 255, 28), width=2)


def save(img, name):
    p = os.path.join(OUT, name)
    img.save(p)
    print("saved", p)


# ---------------------------------------------------------------- A 示波器
def icon_scope():
    img, d = canvas()
    body(d, (30, 37, 48, 255), (86, 102, 118, 255))

    # 屏幕
    d.rounded_rectangle([30, 30, 226, 152], radius=12, fill=(9, 26, 32, 255),
                        outline=(64, 84, 96, 255), width=3)
    # 网格
    for x in range(54, 226, 24):
        d.line([x, 36, x, 146], fill=(22, 58, 66, 255), width=1)
    for y in range(60, 146, 24):
        d.line([36, y, 220, y], fill=(22, 58, 66, 255), width=1)
    # 方波 (青色)
    wy, amp = 94, 30
    pts = [(40, wy)]
    x = 40
    while x < 216:
        pts += [(x, wy - amp), (x + 22, wy - amp), (x + 22, wy + amp),
                (x + 44, wy + amp)]
        x += 44
    d.line(pts, fill=(79, 209, 224, 255), width=6, joint="curve")

    # 底部: 两旋钮 + 电源灯
    for cx, ind in ((74, -50), (182, 40)):
        d.ellipse([cx - 20, 178, cx + 20, 218], fill=(44, 55, 70, 255),
                  outline=(110, 128, 146, 255), width=3)
        a = math.radians(ind)
        d.line([cx, 198, cx + 13 * math.cos(a), 198 + 13 * math.sin(a)],
               fill=(180, 200, 214, 255), width=4)
    d.ellipse([121, 191, 135, 205], fill=(53, 224, 138, 255))
    save(img, "A_示波器.png")


# ---------------------------------------------------------------- B 数字面板
def icon_panel():
    img, d = canvas()
    body(d, (22, 30, 42, 255), (88, 106, 124, 255))

    # 数码管显示屏
    d.rounded_rectangle([30, 32, 226, 118], radius=10, fill=(6, 24, 15, 255),
                        outline=(60, 86, 72, 255), width=3)
    try:
        font = ImageFont.truetype("C:/Windows/Fonts/arialbd.ttf", 58)
    except OSError:
        font = ImageFont.load_default()
    d.text((128, 74), "5.00", font=font, fill=(64, 226, 140, 255), anchor="mm")

    # 香蕉插座 (红/黑) + 旋钮
    d.ellipse([46, 146, 86, 186], fill=(26, 34, 46, 255),
              outline=(110, 128, 146, 255), width=3)
    d.ellipse([58, 158, 74, 174], fill=(224, 74, 74, 255))
    d.ellipse([96, 146, 136, 186], fill=(26, 34, 46, 255),
              outline=(110, 128, 146, 255), width=3)
    d.ellipse([108, 158, 124, 174], fill=(30, 34, 40, 255))
    d.ellipse([168, 140, 226, 198], fill=(40, 52, 66, 255),
              outline=(116, 134, 152, 255), width=3)
    a = math.radians(-60)
    d.line([197, 169, 197 + 22 * math.cos(a), 169 + 22 * math.sin(a)],
           fill=(190, 208, 222, 255), width=5)
    save(img, "B_数字面板.png")


# ---------------------------------------------------------------- C 表盘
def icon_gauge():
    img, d = canvas()
    body(d, (28, 34, 44, 255), (88, 104, 120, 255))

    cx, cy, r = 128, 136, 88
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(14, 20, 28, 255),
              outline=(78, 96, 114, 255), width=4)
    # 刻度 (-130° ~ 130°)
    for i in range(11):
        a = math.radians(-130 + i * 26)
        x1, y1 = cx + (r - 14) * math.cos(a), cy + (r - 14) * math.sin(a)
        x2, y2 = cx + (r - 4) * math.cos(a), cy + (r - 4) * math.sin(a)
        w = 4 if i % 5 == 0 else 2
        col = (190, 204, 218, 255) if i % 5 == 0 else (110, 128, 146, 255)
        d.line([x1, y1, x2, y2], fill=col, width=w)
    # 指针 (橙)
    a = math.radians(-52)
    d.line([cx - 10 * math.cos(a), cy - 10 * math.sin(a),
            cx + (r - 22) * math.cos(a), cy + (r - 22) * math.sin(a)],
           fill=(240, 168, 60, 255), width=7)
    d.ellipse([cx - 12, cy - 12, cx + 12, cy + 12], fill=(240, 168, 60, 255),
              outline=(28, 34, 44, 255), width=3)
    # 底部小电源灯
    d.ellipse([121, 226, 135, 240], fill=(53, 224, 138, 255))
    save(img, "C_表盘.png")


# ---------------------------------------------------------------- D 波形徽标
def icon_wave():
    img, d = canvas()
    body(d, (24, 28, 38, 255), (92, 108, 126, 255))
    # 大方波贯穿
    wy, amp = 128, 52
    pts = [(24, wy)]
    x = 24
    while x < 232:
        pts += [(x, wy - amp), (x + 26, wy - amp), (x + 26, wy + amp),
                (x + 52, wy + amp)]
        x += 52
    # 分段着色: 电压蓝 电流红 功率绿 各画一段
    colors = [(79, 209, 224, 255), (240, 106, 106, 255), (108, 200, 122, 255)]
    seg = len(pts) // 3 + 1
    for i in range(3):
        chunk = pts[i * seg:(i + 1) * seg + 1]
        if len(chunk) >= 2:
            d.line(chunk, fill=colors[i], width=10, joint="curve")
    # 四角螺丝
    for cx, cy in ((34, 34), (222, 34), (34, 222), (222, 222)):
        d.ellipse([cx - 7, cy - 7, cx + 7, cy + 7], fill=(52, 62, 76, 255),
                  outline=(120, 138, 156, 255), width=2)
    save(img, "D_波形徽标.png")


if __name__ == "__main__":
    icon_scope()
    icon_panel()
    icon_gauge()
    icon_wave()
    print("done")
