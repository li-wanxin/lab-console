"""记录导出模块
================
从 app.py 拆出的记录采样导出实现: 把一条已结束的记录写成
.xlsx 工作簿 —— [概览(可选,第一页)] + 数据 + [曲线图(可选,最后页)]。

数据表列定义 (rec_columns) 只依赖 rec 字典里的 role/outputs 结构,
后续新增仪器角色时在 rec_columns 里补一个分支即可, 其余 (统计/图表)
全部按列定义自动适配。

图表绘制经验 (2026-11-07 真机排查, 勿回退):
  1. 文本列 ("工作模式") 混入折线图会让 Excel 渲染异常 (整图无曲线);
  2. 类别轴直接引用 datetime 列时 openpyxl 生成 numRef, Excel 显示
     内部序列号而非时间 —— 需要时间文本辅助列做类别源;
  3. openpyxl 默认 x_axis.axPos='l' 且缺 tickLblPos/delete, 轴会错位/隐藏,
     必须显式 axPos='b'、tickLblPos、delete=False;
  4. 双 Y 轴: 次轴组 axId=200 + crosses='max' 且关闭自身网格;
  5. 图例/绘图区用显式 ManualLayout 分区, 否则渲染器不自动让位会叠字。
"""

from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path

try:
    import openpyxl
    from openpyxl.chart import LineChart, Reference
    from openpyxl.chart.axis import ChartLines
    from openpyxl.chart.layout import Layout, ManualLayout
    from openpyxl.styles import Font, PatternFill
    from openpyxl.utils import get_column_letter
    HAS_XLSX = True
except ImportError:                                    # 未装 openpyxl 时记录功能降级
    HAS_XLSX = False

DEFAULT_INTERVAL = 0.8        # 与 app.POLL_INTERVAL 一致 (避免循环导入)


# --------------------------------------------------------------------------- #
# 取值辅助
# --------------------------------------------------------------------------- #
def combined_point(d: dict, o: dict) -> list:
    """逻辑输出取值：单通道直读；组合按串联 ΣV/min I、并联 V/ΣI。d 为该机采样。"""
    chs = o["chs"]
    if len(chs) == 1:
        return list(d.get(f"ch{chs[0]}") or [None, None, None])
    A = d.get(f"ch{chs[0]}")
    B = d.get(f"ch{chs[1]}")
    if not A or not B:
        return [None, None, None]
    if o["series"]:
        return [A[0] + B[0], min(A[1], B[1]), A[2] + B[2]]
    return [A[0], A[1] + B[1], A[2] + B[2]]


def csv_time(t: float) -> str:
    """时间列文本：精确到毫秒（记录间隔可小于 1s，秒级会重复）。"""
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t)) + \
        f".{int(t * 1000) % 1000:03d}"


def _xlsm_time(t: float) -> datetime:
    return datetime.fromtimestamp(t)


def rec_columns(rec: dict) -> list:
    """数据表列定义: [(表头, 取值函数)]。取值函数输入行 h 返回数值或 None。"""
    if rec["role"] == "power":
        cols = [("时间", None)]
        for o in rec["outputs"]:
            for suffix, idx in (("电压_V", 0), ("电流_A", 1), ("功率_W", 2)):
                cols.append((f"{o['label']}_{suffix}",
                             lambda h, _o=o, _i=idx:
                                 (combined_point(h["d"], _o) or [None] * 3)[_i]))
        return cols
    return [("时间", None), ("电压_V", lambda h: h["d"]["load"][0]),
            ("电流_A", lambda h: h["d"]["load"][1]),
            ("功率_W", lambda h: h["d"]["load"][2]),
            ("工作模式", lambda h: h["d"].get("lset", ["", ""])[0]),
            ("设定值", lambda h: h["d"].get("lset", ["", ""])[1])]


# --------------------------------------------------------------------------- #
# 工作簿写出
# --------------------------------------------------------------------------- #
def write_recording_xlsx(rec: dict, rows: list,
                         overview: bool = True, chart: bool = True) -> None:
    """导出 .xlsx: [概览(可选,第一页)] + 数据 + [曲线图(可选,最后页)]。"""
    if not HAS_XLSX:
        raise RuntimeError("未安装 openpyxl, 无法导出 Excel")
    cols = rec_columns(rec)
    num_cols = [c for c in cols if c[1] is not None and c[0] != "工作模式"]

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "数据"

    # ---- 数据表 ----
    ws.append([c[0] for c in cols])
    for h in rows:
        line = [_xlsm_time(h["t"])]
        for c in cols[1:]:
            try:
                v = c[1](h)
            except Exception:  # noqa: BLE001
                v = None
            line.append(v)
        ws.append(line)
    # 时间列格式: 毫秒精度 (记录间隔可小于 1s)
    for r in range(2, len(rows) + 2):
        ws.cell(row=r, column=1).number_format = "yyyy-mm-dd hh:mm:ss.000"
    # 宽度 24: 时间串 "yyyy-mm-dd hh:mm:ss.000" 共 23 字符, 窄了会显示成 ####
    ws.column_dimensions["A"].width = 24
    for i in range(2, len(cols) + 1):
        ws.column_dimensions[get_column_letter(i)].width = 14
    for cell in ws[1]:
        cell.font = Font(bold=True)
        cell.fill = PatternFill("solid", fgColor="DDEBF7")
    ws.freeze_panes = "A2"

    # ---- 概览 (第一页) ----
    if overview:
        ov = wb.create_sheet("概览", 0)
        t0s = csv_time(rec["t0"]) if rows else "—"
        t1s = csv_time(rows[-1]["t"]) if rows else "—"
        iv = rec.get("interval", DEFAULT_INTERVAL)
        outs_txt = " + ".join(o["label"] for o in rec["outputs"]) if rec["role"] == "power" \
            else "电压 / 电流 / 功率 (含工作模式)"
        info = [
            ("测试概览", None),
            ("仪器", f"{rec.get('dev_name', '')}（{rec.get('dev_model', '')}）"),
            ("仪器标识", rec.get("dev_idn", "") or "—"),
            ("记录对象", outs_txt),
            ("开始时间", t0s),
            ("结束时间", t1s),
            ("采样点数", len(rows)),
            ("采样间隔", f"{iv:g} 秒"),
            ("生成时间", csv_time(time.time())),
        ]
        for k, v in info:
            ov.append([k, v])
        ov["A1"].font = Font(bold=True, size=14)
        for r in range(2, len(info) + 1):
            ov.cell(row=r, column=1).font = Font(bold=True)
        ov.column_dimensions["A"].width = 14
        ov.column_dimensions["B"].width = 46

        # 统计表
        stat_row = len(info) + 2
        ov.cell(row=stat_row, column=1, value="统计").font = Font(bold=True, size=12)
        head = ["通道", "最小值", "最大值", "平均值"]
        for j, htxt in enumerate(head, start=1):
            c = ov.cell(row=stat_row + 1, column=j, value=htxt)
            c.font = Font(bold=True)
            c.fill = PatternFill("solid", fgColor="DDEBF7")
        for i, (title, fn) in enumerate(num_cols):
            vals = []
            for h in rows:
                try:
                    v = fn(h)
                except Exception:  # noqa: BLE001
                    v = None
                if isinstance(v, (int, float)):
                    vals.append(float(v))
            r = stat_row + 2 + i
            ov.cell(row=r, column=1, value=title)
            if vals:
                ov.cell(row=r, column=2, value=min(vals))
                ov.cell(row=r, column=3, value=max(vals))
                ov.cell(row=r, column=4, value=sum(vals) / len(vals))
            else:
                ov.cell(row=r, column=2, value="—")
                ov.cell(row=r, column=3, value="—")
                ov.cell(row=r, column=4, value="—")
        tip = ov.cell(row=stat_row + 2 + len(num_cols) + 1, column=1,
                      value="详细数据见「数据」表" + ("；曲线见「曲线图」表。" if chart else "。"))
        tip.font = Font(italic=True, color="808080")
        ov.column_dimensions["C"].width = 12
        ov.column_dimensions["D"].width = 12

    # ---- 曲线图 (最后一页): 单张图双 Y 轴, 电压/功率左轴 + 电流右轴 ----
    if chart and rows:
        tcol = len(cols) + 1                    # 辅助列: 时间文本 (类别源)
        hc = ws.cell(row=1, column=tcol, value="时间(曲线用)")
        hc.font = Font(bold=True, color="808080")
        for i, h in enumerate(rows, start=2):
            ws.cell(row=i, column=tcol,
                    value=time.strftime("%H:%M:%S", time.localtime(h["t"])))
        ws.column_dimensions[get_column_letter(tcol)].width = 11

        ch = wb.create_sheet("曲线图")
        ch.sheet_view.showGridLines = False

        # 每张图 = 双 Y 轴 (电压/功率 左轴, 电流 右轴);
        # 电源多路记录时 **每一路单独一张图** (2026-11-07 用户要求),
        # 负载单路保持一张总图。列匹配用前缀, 避免多路串图。
        def _cols_for(prefix, key):
            return [i for i, c in enumerate(cols)
                    if c[0].startswith(prefix) and key in c[0]]

        def _build_dual_chart(title, prefix):
            """构建一张双轴折线图: 左轴电压+功率, 右轴电流。"""
            nch = LineChart()
            nch.title = title
            nch.style = 2
            nch.height, nch.width = 12, 28
            # 显式 ManualLayout 分区 (用户三次截图反馈定稿):
            #   标题(顶部自动) / 绘图区 0.12~0.76 / 时间标签 0.76~0.90 /
            #   图例 0.90~0.98, 左右各留 0.12 给轴标题与数字。
            nch.layout = Layout(manualLayout=ManualLayout(
                xMode="edge", yMode="edge", layoutTarget="inner",
                x=0.12, y=0.14, w=0.78, h=0.62))
            nch.y_axis.title = "电压·功率 (V·W)"
            nch.y_axis.number_format = "0.000"
            nch.y_axis.majorTickMark = "out"
            nch.x_axis.delete = False
            nch.y_axis.delete = False
            nch.x_axis.axPos = "b"
            nch.y_axis.axPos = "l"
            nch.x_axis.tickLblPos = "nextTo"
            nch.y_axis.tickLblPos = "nextTo"
            nch.x_axis.majorTickMark = "out"
            nch.x_axis.tickLblSkip = max(1, len(rows) // 10)
            nch.x_axis.tickMarkSkip = max(1, len(rows) // 10)
            nch.y_axis.majorGridlines = ChartLines()
            nch.y_axis.crosses = "autoZero"

            def _add(target, key, color):
                added = 0
                for idx in _cols_for(prefix, key):
                    ref = Reference(ws, min_col=idx + 1, max_col=idx + 1,
                                    min_row=1, max_row=len(rows) + 1)
                    target.add_data(ref, titles_from_data=True)
                    s = target.series[-1]
                    s.smooth = False
                    s.graphicalProperties.line.width = 18000  # ≈1.4pt
                    s.graphicalProperties.line.solidFill = color
                    added += 1
                return added

            si = _add(nch, "电压", "2E75B6")     # 蓝
            si += _add(nch, "功率", "548235")    # 绿
            nch.set_categories(Reference(ws, min_col=tcol, min_row=2,
                                         max_row=len(rows) + 1))

            right = LineChart()
            right.y_axis.axId = 200
            right.y_axis.title = "电流 (A)"
            right.y_axis.number_format = "0.000"
            right.y_axis.majorTickMark = "out"
            right.y_axis.delete = False
            right.y_axis.axPos = "r"
            right.y_axis.tickLblPos = "nextTo"
            right.y_axis.crosses = "max"               # 贴右侧
            right.y_axis.majorGridlines = None         # 网格只用左轴的
            ri = _add(right, "电流", "C00000")          # 红
            right.set_categories(Reference(ws, min_col=tcol, min_row=2,
                                           max_row=len(rows) + 1))
            nch += right                               # 合并为双轴图

            if si + ri <= 1:
                nch.legend = None
            else:
                nch.legend.position = "b"
                # 图例固定在最底部一条, 与时间标签分区 (不依赖渲染器自动布局)
                nch.legend.layout = Layout(manualLayout=ManualLayout(
                    xMode="edge", yMode="edge",
                    x=0.12, y=0.90, w=0.78, h=0.08))
            return nch

        # 图规格: 负载一张总图; 电源每个逻辑输出一张
        if rec["role"] == "power":
            specs = [(f"{o['label']} 记录曲线", f"{o['label']}_")
                     for o in rec["outputs"]]
        else:
            specs = [("U / I / W 记录曲线", "")]

        anchors = [f"B{2 + k * 25}" for k in range(len(specs))]
        for k, (title, prefix) in enumerate(specs):
            ch.add_chart(_build_dual_chart(title, prefix), anchors[k])
        note = ch.cell(row=2 + len(specs) * 25, column=2,
                       value="双击图表可在 Excel 中编辑；左轴=电压/功率，右轴=电流；数据源为「数据」表。")
        note.font = Font(italic=True, color="808080")

    Path(rec["path"]).parent.mkdir(parents=True, exist_ok=True)
    wb.save(rec["path"])
