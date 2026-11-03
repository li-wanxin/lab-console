#!/usr/bin/env python3
"""
ITECH 台式电源 / 电子负载 上位机
================================
IT6302 三路直流电源 + IT8511A+ 电子负载

用法:
    python app.py                 # 启动并自动打开浏览器 (http://127.0.0.1:8765)
    python app.py --port 9000 --no-browser

无硬件时可直接选"模拟模式"体验全部功能。
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import sys
import threading
import time
import traceback
import webbrowser
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

from instruments import (
    HAS_SERIAL,
    IT6302,
    IT8511A,
    LOAD_DRIVERS,
    LOAD_MODEL_SPECS,
    MODEL_CATALOG,
    POWER_MODEL_SPECS,
    InstrumentError,
    SerialTransport,
    SimLoad,
    SimPower,
    list_serial_ports,
)

if getattr(sys, "frozen", False):          # PyInstaller 打包后资源在临时解包目录
    BASE_DIR = Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
else:
    BASE_DIR = Path(__file__).resolve().parent
WEB_DIR = BASE_DIR / "web"

POLL_INTERVAL = 0.8          # 轮询周期 (秒)
HISTORY_POINTS = 20000       # 数据缓存点数 (约 4.4 小时 @0.8s)
CHART_POINTS = 360           # 界面曲线降采样上限（全量历史均匀取点）
TABLE_POINTS = 200           # 数据表取最近这么多原始点


def list_dirs(p: str) -> dict:
    """目录选择器用：列出 p 的子目录；p 为空时列盘符。仅本机监听，无注入面。"""
    if not p:
        drives = [f"{c}:\\" for c in "CDEFGHIJKLMNOPQRSTUVWXYZ"
                  if Path(f"{c}:\\").exists()]
        return {"path": "", "parent": None, "dirs": drives}
    base = Path(p)
    if not base.exists():
        raise FileNotFoundError(f"路径不存在: {base}")
    if not base.is_dir():
        raise NotADirectoryError(f"不是目录: {base}")
    dirs = []
    try:
        for child in sorted(base.iterdir(), key=lambda x: x.name.lower()):
            try:
                if child.is_dir():
                    dirs.append(child.name)
            except OSError:
                pass   # 无权限/系统保留项跳过
    except PermissionError:
        pass
    parent = str(base.parent) if base.parent != base else None
    return {"path": str(base), "parent": parent, "dirs": dirs}


# --------------------------------------------------------------------------- #
# 业务层
# --------------------------------------------------------------------------- #
class Dev:
    """一台仪器实例（电源或负载），可任意数量并存。"""
    _seq = 0

    def __init__(self, role: str, model: str, name: str):
        Dev._seq += 1
        self.id = f"d{Dev._seq}"
        self.role = role                # 'power' | 'load'
        self.model = model
        self.name = name
        self.driver = None
        self.meta = {"connected": False, "sim": False, "port": "", "baud": 0, "idn": "", "error": ""}
        if role == "power":
            self.channels = {
                n: {"vset": 0.0, "iset": 0.1, "on": False, "v": 0.0, "i": 0.0, "p": 0.0}
                for n in (1, 2, 3)
            }
            self.range = None
            self.state = None
        else:
            self.channels = None
            self.range = dict(LOAD_MODEL_SPECS.get(model) or LOAD_MODEL_SPECS["IT8511A+"])
            self.state = {"mode": "CC", "level": 0.5, "on": False, "v": 0.0, "i": 0.0, "p": 0.0}

    def payload(self) -> dict:
        d = {"id": self.id, "role": self.role, "name": self.name, "model": self.model, **self.meta}
        if self.role == "power":
            d["channels"] = self.channels
        else:
            d["range"] = self.range
            d.update(self.state)
        return d

    def sample(self) -> dict:
        """一条历史采样的本机数据。"""
        if self.role == "power":
            return {f"ch{n}": [self.channels[n]["v"], self.channels[n]["i"], self.channels[n]["p"]]
                    for n in (1, 2, 3)}
        return {"load": [self.state["v"], self.state["i"], self.state["p"]],
                "lset": [self.state["mode"], self.state["level"]]}


class Bench:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.devices: dict = {}                      # dev_id -> Dev
        self.limits = {"max_v": 30.0, "max_i": 3.0, "max_p": 150.0, "auto_trip": True}
        self.history: deque = deque(maxlen=HISTORY_POINTS)
        self.alerts: deque = deque(maxlen=60)
        self.recordings: dict = {}                   # id -> {id, dev, role, ...}
        self._rec_seq = 0
        self.sweep = {
            "running": False,
            "type": "load",
            "points": [],
            "progress": 0,
            "total": 0,
            "message": "",
        }
        self._sweep_stop = threading.Event()
        self._busy = False
        self._poll_thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._poll_thread.start()
        # 默认清单：一台电源 + 一台负载（可再增删）
        self.add_instrument("power")
        self.add_instrument("load")

    # ---------------- 日志 ----------------
    def log(self, level: str, text: str) -> None:
        self.alerts.appendleft({"t": time.time(), "level": level, "text": text})

    # ---------------- 设备查找 ----------------
    def get_dev(self, dev_id) -> Dev:
        dev = self.devices.get(str(dev_id))
        if dev is None:
            raise InstrumentError("设备不存在（可能已被删除）")
        return dev

    # ---------------- 仪器清单：添加 / 删除 ----------------
    def add_instrument(self, role: str, model: str | None = None) -> dict:
        with self.lock:
            if role not in ("power", "load"):
                raise InstrumentError(f"未知设备角色: {role}")
            catalog = MODEL_CATALOG.get(role) or []
            if model is None:
                model = catalog[0] if catalog else ""
            if model not in catalog:
                raise InstrumentError(f"不支持型号 {model}，可选: {', '.join(catalog)}")
            # 命名取同类设备最大序号 + 1（删除后重加不会与现存设备重名）
            mx = 0
            for d in self.devices.values():
                if d.role == role:
                    try:
                        mx = max(mx, int(d.name.rsplit(" ", 1)[1]))
                    except (ValueError, IndexError):
                        mx = max(mx, sum(1 for x in self.devices.values() if x.role == role))
            n = mx + 1
            dev = Dev(role, model, f"电源 {n}" if role == "power" else f"负载 {n}")
            self.devices[dev.id] = dev
            self.log("ok", f"已添加仪器: {dev.name} · {model}（未连接）")
            return self.snapshot()

    def reorder_instruments(self, ids: list) -> dict:
        """按前端给出的 id 顺序重排清单（拖动排序）。"""
        with self.lock:
            order = [str(i) for i in (ids or [])]
            if sorted(order) != sorted(self.devices.keys()):
                raise InstrumentError("设备清单不匹配，无法排序")
            self.devices = {i: self.devices[i] for i in order}
            return self.snapshot()

    def remove_instrument(self, dev_id: str) -> dict:
        with self.lock:
            dev = self.get_dev(dev_id)
            if self.sweep.get("running"):
                self._sweep_stop.set()
            self._disconnect_dev(dev, keep_log=True)
            del self.devices[dev.id]
            self.log("warn", f"已从清单删除仪器: {dev.name} · {dev.model}")
            return self.snapshot()

    # ---------------- 连接 ----------------
    def connect(self, dev_id: str, port: str, baud: int, sim: bool,
                safe_start: bool = True, model: str | None = None) -> dict:
        with self.lock:
            dev = self.get_dev(dev_id)
            # 负载允许在连接时改型号（未连接状态下才连得上，天然安全）
            if dev.role == "load" and model and model != dev.model:
                if model not in LOAD_MODEL_SPECS:
                    raise InstrumentError(f"未知负载型号: {model}")
                dev.model = model
                dev.range = dict(LOAD_MODEL_SPECS[model])
            self._disconnect_dev(dev, keep_log=True)
            if dev.role == "power":
                if sim:
                    drv = SimPower()
                    # 与清单里第一台模拟负载联动（CH1 拉载演示）
                    for d in self.devices.values():
                        if d.role == "load" and d.driver is not None and d.meta.get("sim"):
                            drv.load = d.driver
                            d.driver.power = drv
                            break
                else:
                    drv = IT6302(SerialTransport(port, baud))
                dev.driver = drv
                dev.meta.update({"connected": True, "sim": bool(sim),
                                 "port": "SIM" if sim else port, "baud": baud if not sim else 0,
                                 "idn": drv.idn, "error": ""})
                self._read_power_setpoints(dev)
                if safe_start:
                    self._power_all_off(dev)
                self.log("ok", f"{dev.name} 已连接: {dev.meta['idn']} @ {dev.meta['port']}")
            else:
                if sim:
                    drv = SimLoad(model=dev.model)
                    for d in self.devices.values():
                        if (d.role == "power" and d.driver is not None and d.meta.get("sim")
                                and getattr(d.driver, "load", None) is None):
                            d.driver.load = drv
                            drv.power = d.driver
                            break
                else:
                    if getattr(LOAD_DRIVERS[dev.model], "LEGACY_SERIAL", False):
                        drv = LOAD_DRIVERS[dev.model](port, baud)
                    else:
                        drv = LOAD_DRIVERS[dev.model](SerialTransport(port, baud))
                dev.driver = drv
                dev.meta.update({"connected": True, "sim": bool(sim),
                                 "port": "SIM" if sim else port, "baud": baud if not sim else 0,
                                 "idn": drv.idn, "error": ""})
                if safe_start:
                    drv.set_input(False)
                    dev.state["on"] = False
                self.log("ok", f"{dev.name}[{dev.model}] 已连接: {dev.meta['idn']} @ {dev.meta['port']}")
            return self.snapshot()

    def disconnect(self, dev_id: str) -> dict:
        with self.lock:
            dev = self.get_dev(dev_id)
            self._disconnect_dev(dev)
            return self.snapshot()

    def _disconnect_dev(self, dev: Dev, keep_log: bool = False) -> None:
        if dev.driver is not None:
            try:
                if dev.role == "power":
                    self._power_all_off(dev)
                else:
                    dev.driver.set_input(False)
                dev.driver.local()
            except Exception:  # noqa: BLE001
                pass
            try:
                dev.driver.close()
            except Exception:  # noqa: BLE001
                pass
            drv, dev.driver = dev.driver, None
            # 解除模拟器之间的联动引用
            for d in self.devices.values():
                if d.role == "power" and d.driver is not None and getattr(d.driver, "load", None) is drv:
                    d.driver.load = None
                if d.role == "load" and d.driver is not None and getattr(d.driver, "power", None) is drv:
                    d.driver.power = None
        dev.meta.update({"connected": False, "sim": False, "port": "", "baud": 0, "idn": "", "error": ""})
        if dev.role == "power":
            for ch in dev.channels.values():
                ch.update({"v": 0.0, "i": 0.0, "p": 0.0, "on": False})
        else:
            dev.state.update({"v": 0.0, "i": 0.0, "p": 0.0, "on": False})
        if not keep_log:
            self.log("info", f"{dev.name} 已断开")

    def _power_all_off(self, dev: Dev) -> None:
        if dev.driver is None:
            return
        for ch in (1, 2, 3):
            try:
                dev.driver.set_output(ch, False)
                dev.channels[ch]["on"] = False
            except Exception:  # noqa: BLE001
                pass

    def _read_power_setpoints(self, dev: Dev) -> None:
        if dev.driver is None:
            return
        for ch in (1, 2, 3):
            try:
                v, i = dev.driver.query_setpoints(ch)
                dev.channels[ch]["vset"] = v
                dev.channels[ch]["iset"] = i
            except Exception:  # noqa: BLE001
                pass

    # ---------------- 电源控制 ----------------
    def power_set(self, dev_id: str, ch: int, volts: float | None, amps: float | None) -> dict:
        with self.lock:
            dev = self.get_dev(dev_id)
            if dev.role != "power" or dev.driver is None:
                raise InstrumentError("电源未连接")
            st = dev.channels[ch]
            if volts is not None:
                vmax = dev.driver.MAX_V[ch]
                if volts < 0 or volts > vmax:
                    raise InstrumentError(f"CH{ch} 电压超出范围 0~{vmax}V")
                dev.driver.set_voltage(ch, volts)
                st["vset"] = volts
            if amps is not None:
                if amps <= 0 or amps > dev.driver.MAX_I:
                    raise InstrumentError(f"CH{ch} 电流上限超出范围 0~{dev.driver.MAX_I}A")
                dev.driver.set_current(ch, amps)
                st["iset"] = amps
            return self.snapshot()

    def power_output(self, dev_id: str, ch: int | None, on: bool) -> dict:
        with self.lock:
            dev = self.get_dev(dev_id)
            if dev.role != "power" or dev.driver is None:
                raise InstrumentError("电源未连接")
            targets = (1, 2, 3) if ch is None else (ch,)
            for c in targets:
                dev.driver.set_output(c, on)
                dev.channels[c]["on"] = on
            self.log("warn" if on else "info",
                     f"{dev.name} CH{','.join(map(str, targets))} 输出 {'开启' if on else '关闭'}")
            return self.snapshot()

    # ---------------- 负载控制 ----------------
    def load_set(self, dev_id: str, mode: str, value: float) -> dict:
        with self.lock:
            dev = self.get_dev(dev_id)
            if dev.role != "load" or dev.driver is None:
                raise InstrumentError("负载未连接")
            mode = mode.upper()
            if mode not in ("CC", "CV", "CR", "CW"):
                raise InstrumentError(f"非法模式: {mode}")
            self._check_load_range(dev, mode, value)
            # 与电源 v54 行为一致: 输入接通时改模式/改设定, 必须先关输入,
            # 改完保持 OFF, 由用户手动再开 (避免带载切档冲击)
            was_on = bool(dev.state.get("on"))
            if was_on:
                dev.driver.set_input(False)
                dev.state["on"] = False
                self.log("warn", f"{dev.name} 改设定前已自动关闭输入, 请手动重新开启")
            dev.driver.set_mode(mode)
            dev.driver.set_level(mode, value)
            dev.state["mode"] = mode
            dev.state["level"] = value
            return self.snapshot()

    def load_input(self, dev_id: str, on: bool) -> dict:
        with self.lock:
            dev = self.get_dev(dev_id)
            if dev.role != "load" or dev.driver is None:
                raise InstrumentError("负载未连接")
            dev.driver.set_input(on)
            dev.state["on"] = bool(on)
            self.log("warn" if on else "info", f"{dev.name} 输入 {'开启' if on else '关闭'}")
            return self.snapshot()

    @staticmethod
    def _check_load_range(dev: Dev, mode: str, value: float) -> None:
        r = dev.range
        if mode == "CC" and not (0 < value <= r["max_i"]):
            raise InstrumentError(f"CC 电流需在 0~{r['max_i']:g}A")
        if mode == "CV" and not (0 < value <= r["max_v"]):
            raise InstrumentError(f"CV 电压需在 0~{r['max_v']:g}V")
        if mode == "CW" and not (0 < value <= r["max_p"]):
            raise InstrumentError(f"CW 功率需在 0~{r['max_p']:g}W")
        if mode == "CR" and not (0.01 <= value <= r["max_r"]):
            raise InstrumentError(f"CR 电阻需在 0.01~{r['max_r']:g}Ω")

    def selftest(self, dev_id: str) -> dict:
        """逐条发查询, 检查串口链路与命令兼容性。"""
        with self.lock:
            dev = self.get_dev(dev_id)
            if dev.driver is None:
                raise InstrumentError("该设备尚未连接")
            driver = dev.driver
            role = dev.role
            lines: list = []
            err = ""

            def q(cmd: str) -> str:
                try:
                    r = driver.raw_query(cmd)
                except Exception as exc:  # noqa: BLE001
                    lines.append(f"{cmd} → 无响应 ({exc})")
                    raise
                lines.append(f"{cmd} → {r}")
                return r

            try:
                if hasattr(driver, "selftest_lines"):
                    # 老款二进制协议负载 (IT8510): 专用自检, 不走 SCPI 文本
                    for line in driver.selftest_lines():
                        lines.append(line)
                else:
                    q("*IDN?")
                    if role == "power":
                        q("SYST:VERS?")
                        for c in (1, 2, 3):
                            driver.select(c)
                            q("VOLT?")
                            q("CURR?")
                    else:
                        q("FUNC?")
                        q("INP?")
                        q("MEAS:VOLT?")
            except Exception as exc:  # noqa: BLE001
                err = str(exc)

            for line in reversed(lines):
                self.log("info", f"{dev.name}自检 · {line}")
            self.log("ok" if not err else "error",
                     f"{dev.name}自检{'通过' if not err else '未通过: ' + err}")
            return {"ok": not err, "lines": lines, "error": err}

    def estop(self) -> dict:
        self._sweep_stop.set()
        with self.lock:
            for dev in self.devices.values():
                if dev.role == "power":
                    self._power_all_off(dev)
                elif dev.driver is not None:
                    try:
                        dev.driver.set_input(False)
                    except Exception:  # noqa: BLE001
                        pass
                    dev.state["on"] = False
            self.log("error", "紧急停止: 已关闭所有电源输出与负载输入")
            return self.snapshot()

    # ---------------- 轮询 ----------------
    def _poll_loop(self) -> None:
        while True:
            time.sleep(POLL_INTERVAL)
            if self._busy:
                continue
            self.poll_once()

    def poll_once(self) -> None:
        with self.lock:
            for dev in self.devices.values():
                if dev.driver is None:
                    continue
                if dev.role == "power":
                    for ch in (1, 2, 3):
                        try:
                            v, i = dev.driver.measure(ch)
                            st = dev.channels[ch]
                            # 全精度: 不做 round 截断, 保留仪器回读原始值
                            st["v"] = v
                            st["i"] = i
                            st["p"] = v * i
                            dev.meta["error"] = ""
                        except Exception as exc:  # noqa: BLE001
                            dev.meta["error"] = str(exc)
                else:
                    try:
                        v, i, p = dev.driver.measure()
                        dev.state.update({"v": v, "i": i, "p": p})
                        dev.meta["error"] = ""
                    except Exception as exc:  # noqa: BLE001
                        dev.meta["error"] = str(exc)

            self._safety_check()
            h = {"t": time.time(), "devs": {d.id: d.sample() for d in self.devices.values()}}
            self.history.append(h)
            if self.recordings:
                for rec in self.recordings.values():
                    dev = self.devices.get(rec["dev"])
                    if dev is not None and h["t"] + 1e-9 >= rec.get("next_t", 0):
                        rec["rows"].append({"t": h["t"], "d": h["devs"][dev.id]})
                        step = rec["interval"]
                        nxt = rec["next_t"] + step
                        while nxt <= h["t"]:   # 中断后追帧，避免密集补点
                            nxt += step
                        rec["next_t"] = nxt

    def _safety_check(self) -> None:
        """软件保护: 负载功率/电源电流越限自动脱扣。"""
        if not self.limits.get("auto_trip"):
            return
        lim_p = self.limits["max_p"]
        lim_i = self.limits["max_i"]
        trip = False
        for dev in self.devices.values():
            if dev.role == "load":
                if dev.driver is not None and dev.state["on"] and dev.state["p"] > lim_p:
                    trip = True
                    self.log("error", f"{dev.name} 功率 {dev.state['p']:.1f}W 超过上限 {lim_p}W, 已自动脱扣")
            else:
                for ch in (1, 2, 3):
                    if dev.channels[ch]["on"] and dev.channels[ch]["i"] > lim_i * 1.02:
                        trip = True
                        self.log("error", f"{dev.name} CH{ch} 电流 {dev.channels[ch]['i']:.2f}A 超过上限 {lim_i}A, 已自动脱扣")
        if trip:
            for dev in self.devices.values():
                if dev.role == "power":
                    self._power_all_off(dev)
                elif dev.driver is not None:
                    try:
                        dev.driver.set_input(False)
                    except Exception:  # noqa: BLE001
                        pass
                    dev.state["on"] = False

    # ---------------- 扫描 ----------------
    def start_sweep(self, cfg: dict) -> dict:
        with self.lock:
            if self._busy:
                raise InstrumentError("已有扫描任务在运行")
            pdev = self.get_dev(cfg.get("pdev", ""))
            ldev = self.get_dev(cfg.get("ldev", ""))
            if pdev.role != "power" or ldev.role != "load":
                raise InstrumentError("扫描需要选择一台电源与一台负载")
            if pdev.driver is None or ldev.driver is None:
                raise InstrumentError("扫描需要所选电源与负载同时连接")
            self._sweep_stop.clear()
            self._busy = True
            self.sweep = {
                "running": True,
                "type": cfg.get("type", "load"),
                "points": [],
                "progress": 0,
                "total": 0,
                "message": "准备中",
                "pdev": pdev.id,
                "ldev": ldev.id,
                "pdev_name": pdev.name,
                "ldev_name": ldev.name,
            }
            threading.Thread(target=self._run_sweep, args=(pdev.id, ldev.id, cfg), daemon=True).start()
            return self.snapshot()

    def stop_sweep(self) -> dict:
        self._sweep_stop.set()
        self.sweep["message"] = "正在停止…"
        return self.snapshot()

    def _run_sweep(self, pdev_id: str, ldev_id: str, cfg: dict) -> None:
        self._busy = True
        try:
            with self.lock:
                pdev = self.devices.get(pdev_id)
                ldev = self.devices.get(ldev_id)
                if pdev is None or ldev is None:
                    raise InstrumentError("扫描设备已被删除")
            ch = int(cfg.get("ch", 1))
            dwell = max(0.05, float(cfg.get("dwell_ms", 400)) / 1000.0)
            safe_end = bool(cfg.get("safe_end", True))
            try:
                if cfg.get("type", "load") == "load":
                    self._sweep_load(pdev, ldev, cfg, ch, dwell)
                else:
                    self._sweep_psu(pdev, ldev, cfg, ch, dwell)
            except Exception as exc:  # noqa: BLE001
                self.sweep["message"] = f"扫描中断: {exc}"
                self.log("error", f"扫描中断: {exc}")
                traceback.print_exc()
            finally:
                with self.lock:
                    if safe_end and pdev.driver is not None and ldev.driver is not None:
                        try:
                            pdev.driver.set_output(ch, False)
                            pdev.channels[ch]["on"] = False
                            ldev.driver.set_input(False)
                            ldev.state["on"] = False
                        except Exception:  # noqa: BLE001
                            pass
                    self.sweep["running"] = False
                    if not self.sweep["message"].startswith("扫描中断"):
                        self.sweep["message"] = "已完成" if not self._sweep_stop.is_set() else "已停止"
                    self.log("ok", f"扫描{self.sweep['message']}, 共 {len(self.sweep['points'])} 点")
                    self._busy = False
        except Exception as exc:  # noqa: BLE001
            self._busy = False
            self.sweep["running"] = False
            self.sweep["message"] = f"扫描中断: {exc}"
            self.log("error", f"扫描中断: {exc}")

    def _sweep_load(self, pdev: Dev, ldev: Dev, cfg: dict, ch: int, dwell: float) -> None:
        """负载电流扫描: 电源恒压, 负载 CC 从 I1 扫到 I2, 观察电源输出 V-I 特性。"""
        v_fixed = float(cfg["v_fixed"])
        i_start = float(cfg["start"])
        i_end = float(cfg["end"])
        step = abs(float(cfg["step"])) or 0.1
        i_limit = float(cfg.get("i_limit", 3.0))
        if v_fixed <= 0 or v_fixed > pdev.driver.MAX_V[ch]:
            raise InstrumentError(f"电源电压需在 0~{pdev.driver.MAX_V[ch]}V")
        if i_limit > pdev.driver.MAX_I:
            raise InstrumentError("电源限流值超出范围")
        if i_end <= i_start:
            raise InstrumentError("终止电流需大于起始电流")
        r = ldev.range
        if max(abs(i_start), abs(i_end)) > r["max_i"]:
            raise InstrumentError(f"扫描电流超过负载 {r['max_i']:g}A 上限")
        if v_fixed * min(i_end, i_limit) > r["max_p"] * 1.05:
            raise InstrumentError(f"扫描功率可能超过负载 {r['max_p']:g}W, 请降低电压或电流")

        n = max(1, min(int(round((i_end - i_start) / step)) + 1, 400))

        with self.lock:
            pdev.driver.set_voltage(ch, v_fixed)
            pdev.driver.set_current(ch, i_limit)
            pdev.driver.set_output(ch, True)
            pdev.channels[ch]["on"] = True
            pdev.channels[ch]["vset"] = v_fixed
            pdev.channels[ch]["iset"] = i_limit

            ldev.driver.set_mode("CC")
            ldev.driver.set_input(True)
            ldev.state["on"] = True
            ldev.state["mode"] = "CC"
            self.sweep["total"] = n

        time.sleep(0.3)

        for k in range(n):
            if self._sweep_stop.is_set():
                break
            target = max(0.0, min(i_start + k * step, r["max_i"]))
            with self.lock:
                ldev.driver.set_level("CC", target)
                ldev.state["level"] = target

            time.sleep(dwell)

            if self._sweep_stop.is_set():
                break
            with self.lock:
                pv, pi = pdev.driver.measure(ch)
                lv, li, lp = ldev.driver.measure()
                pdev.channels[ch].update({"v": pv, "i": pi, "p": pv * pi})
                ldev.state.update({"v": lv, "i": li, "p": lp})
                self.sweep["points"].append(
                    {
                        "set": target,
                        "v": lv,
                        "i": li,
                        "p": lp,
                        "vpsu": pv,
                        "ipsu": pi,
                    }
                )
                self.sweep["progress"] = k + 1

            if lp > self.limits["max_p"]:
                self.sweep["message"] = "触发功率保护, 扫描提前结束"
                break

    def _sweep_psu(self, pdev: Dev, ldev: Dev, cfg: dict, ch: int, dwell: float) -> None:
        """电源电压扫描: 负载恒流, 电源电压从 V1 扫到 V2, 观察被测件/回路 I-V 特性。"""
        v_start = float(cfg["start"])
        v_end = float(cfg["end"])
        step = abs(float(cfg["step"])) or 0.1
        i_load = float(cfg["i_load"])
        i_limit = float(cfg.get("i_limit", 3.0))
        if max(v_start, v_end) > pdev.driver.MAX_V[ch]:
            raise InstrumentError(f"电压超过 CH{ch} 上限 {pdev.driver.MAX_V[ch]}V")
        if i_limit > pdev.driver.MAX_I:
            raise InstrumentError("电源限流值超出范围")
        r = ldev.range
        if i_load > r["max_i"]:
            raise InstrumentError(f"负载电流超过 {r['max_i']:g}A")
        if v_end <= v_start:
            raise InstrumentError("终止电压需大于起始电压")

        n = max(1, min(int(round((v_end - v_start) / step)) + 1, 400))

        with self.lock:
            ldev.driver.set_mode("CC")
            ldev.driver.set_level("CC", i_load)
            ldev.driver.set_input(True)
            ldev.state.update({"on": True, "mode": "CC", "level": i_load})
            pdev.driver.set_current(ch, i_limit)
            pdev.channels[ch]["iset"] = i_limit

            pdev.driver.set_voltage(ch, v_start)
            pdev.driver.set_output(ch, True)
            pdev.channels[ch]["on"] = True
            self.sweep["total"] = n

        time.sleep(0.4)

        for k in range(n):
            if self._sweep_stop.is_set():
                break
            target = max(0.0, min(v_start + k * step, pdev.driver.MAX_V[ch]))
            with self.lock:
                pdev.driver.set_voltage(ch, target)
                pdev.channels[ch]["vset"] = target

            time.sleep(dwell)

            if self._sweep_stop.is_set():
                break
            with self.lock:
                pv, pi = pdev.driver.measure(ch)
                lv, li, lp = ldev.driver.measure()
                pdev.channels[ch].update({"v": pv, "i": pi, "p": pv * pi})
                ldev.state.update({"v": lv, "i": li, "p": lp})
                self.sweep["points"].append(
                    {
                        "set": target,
                        "v": lv,
                        "i": li,
                        "p": lp,
                        "vpsu": pv,
                        "ipsu": pi,
                    }
                )
                self.sweep["progress"] = k + 1

            if lp > self.limits["max_p"]:
                self.sweep["message"] = "触发功率保护, 扫描提前结束"
                break

    def sweep_csv(self) -> str:
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["设定值", "负载电压_V", "负载电流_A", "功率_W", "电源电压_V", "电源电流_A"])
        for p in self.sweep["points"]:
            w.writerow([p["set"], p["v"], p["i"], p["p"], p["vpsu"], p["ipsu"]])
        return buf.getvalue()

    def export(self, role: str) -> tuple:
        stamp = time.strftime("%Y%m%d_%H%M%S")
        if role == "sweep":
            return f"IV扫描_{stamp}.csv", self.sweep_csv()
        raise InstrumentError(f"未知数据类型: {role}")

    # ---------------- 记录 ----------------
    def _rec_list(self) -> list:
        return [
            {"id": r["id"], "dev": r["dev"], "role": r["role"], "channels": r["channels"],
             "outputs": r.get("outputs") or [], "interval": r.get("interval", POLL_INTERVAL),
             "t0": r["t0"], "path": r["path"], "count": len(r["rows"])}
            for r in self.recordings.values()
        ]

    def record_start(self, dev_id: str, channels: list, path: str, outputs: list | None = None,
                     interval: float | None = None) -> dict:
        with self.lock:
            dev = self.get_dev(dev_id)
            role = dev.role
            chans: list = []
            rec_outs: list = []
            if role == "power":
                if outputs:
                    for o in outputs:
                        try:
                            chs = sorted({int(c) for c in (o.get("chs") or [])})
                        except (TypeError, ValueError, AttributeError):
                            chs = []
                        chs = [c for c in chs if c in (1, 2, 3)]
                        if not chs:
                            raise InstrumentError("记录输出配置无效")
                        label = str(o.get("label") or ("CH" + "+CH".join(map(str, chs))))
                        rec_outs.append({"chs": chs, "series": bool(o.get("series")), "label": label})
                else:
                    try:
                        chans = sorted({int(c) for c in (channels or [])})
                    except (TypeError, ValueError):
                        chans = []
                    chans = [c for c in chans if c in (1, 2, 3)]
                    rec_outs = [{"chs": [c], "series": False, "label": f"CH{c}"} for c in chans]
                if not rec_outs:
                    raise InstrumentError("请至少选择一个记录通道")
                chans = sorted({c for o in rec_outs for c in o["chs"]})
            path = str(path or "").strip()
            if not path:
                path = f"{dev.name.replace(' ', '')}记录_{time.strftime('%Y%m%d_%H%M%S')}.csv"
            if not path.lower().endswith(".csv"):
                path += ".csv"
            target = Path(path)
            try:
                if str(target.parent) not in ("", "."):
                    target.parent.mkdir(parents=True, exist_ok=True)
            except Exception as exc:  # noqa: BLE001
                raise InstrumentError(f"存储目录不可用: {exc}")
            for r in self.recordings.values():
                if Path(r["path"]) == Path(path):
                    raise InstrumentError(f"该文件已被记录 #{r['id']} 使用，请换个文件名")
            self._rec_seq += 1
            # 记录采样间隔：最小为轮询周期 0.8s，可放宽到 1h 以降低数据量
            try:
                iv = float(interval) if interval not in (None, "", "follow") else POLL_INTERVAL
            except (TypeError, ValueError):
                iv = POLL_INTERVAL
            iv = min(max(iv, POLL_INTERVAL), 3600.0)
            t0 = time.time()
            rec = {"id": self._rec_seq, "dev": dev.id, "role": role, "channels": chans,
                   "outputs": rec_outs, "t0": t0, "path": path, "rows": [],
                   "interval": iv, "next_t": t0 + iv}
            self.recordings[rec["id"]] = rec
            what = " + ".join(o["label"] for o in rec_outs) if role == "power" else dev.name
            self.log("info", f"开始记录 #{rec['id']} · {dev.name} · {what} · {iv:g}s/点 → {path}")
            return {"id": rec["id"], "t0": rec["t0"], "path": path, "interval": iv,
                    "channels": chans, "recordings": self._rec_list()}

    def record_stop(self, rid: int) -> dict:
        with self.lock:
            rec = self.recordings.pop(int(rid), None)
            if rec is None:
                raise InstrumentError("记录不存在或已结束")
            rows = list(rec["rows"])
        try:
            self._write_recording(rec, rows)
        except Exception as exc:  # noqa: BLE001
            raise InstrumentError(f"写出 CSV 失败: {exc}")
        self.log("ok", f"记录 #{rec['id']} 已导出 {len(rows)} 点 → {rec['path']}")
        return {"ok": True, "path": rec["path"], "count": len(rows)}

    @staticmethod
    def _combined_point(d: dict, o: dict) -> list:
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

    @staticmethod
    def _csv_time(t: float) -> str:
        """CSV 时间列：精确到毫秒（记录间隔可小于 1s，秒级会重复）。"""
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t)) + f".{int(t * 1000) % 1000:03d}"

    def _write_recording(self, rec: dict, rows: list) -> None:
        buf = io.StringIO()
        w = csv.writer(buf)
        if rec["role"] == "power":
            head = ["时间"]
            for o in rec["outputs"]:
                head += [f"{o['label']}_电压_V", f"{o['label']}_电流_A", f"{o['label']}_功率_W"]
            w.writerow(head)
            for h in rows:
                line = [self._csv_time(h["t"])]
                for o in rec["outputs"]:
                    line += self._combined_point(h["d"], o)
                w.writerow(line)
        else:
            w.writerow(["时间", "电压_V", "电流_A", "功率_W", "工作模式", "设定值"])
            for h in rows:
                mode, level = h["d"].get("lset", ["", ""])
                w.writerow([
                    self._csv_time(h["t"]),
                    *h["d"]["load"], mode, level,
                ])
        Path(rec["path"]).write_bytes(buf.getvalue().encode("utf-8-sig"))

    # ---------------- 快照 ----------------
    def snapshot(self) -> dict:
        with self.lock:
            raw = list(self.history)
            # 曲线数据：对全量历史均匀降采样到 CHART_POINTS → 横轴时间跨度随记录
            # 时长自动扩展（时间缩放），老数据不会被"刷掉"；表格数据另给最近原始行
            if len(raw) > CHART_POINTS:
                step = -(-len(raw) // CHART_POINTS)          # ceil 整除
                chart = raw[::step]
                if chart[-1] is not raw[-1]:
                    chart.append(raw[-1])                    # 末点保持实时
            else:
                chart = raw
            recent = raw[-TABLE_POINTS:]
            return {
                "ts": time.time(),
                "devices": [d.payload() for d in self.devices.values()],
                "models": MODEL_CATALOG,
                # 各型号串口能力: 前端波特率下拉据此按型号渲染
                "model_specs": {
                    "power": {m: {"baud_rates": s.get("baud_rates", []),
                                  "baud_default": s.get("baud_default", 9600)}
                              for m, s in POWER_MODEL_SPECS.items()},
                    "load": {m: {"baud_rates": s.get("baud_rates", []),
                                 "baud_default": s.get("baud_default", 9600)}
                             for m, s in LOAD_MODEL_SPECS.items()},
                },
                "history": chart,
                "recent": recent,
                "buffered": len(raw),
                "alerts": list(self.alerts)[:30],
                "sweep": self.sweep,
                "recordings": self._rec_list(),
                "ports": list_serial_ports(),
                "has_serial": HAS_SERIAL,
            }


BENCH = Bench()


# --------------------------------------------------------------------------- #
# HTTP 层
# --------------------------------------------------------------------------- #
MIME = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
}


class Handler(BaseHTTPRequestHandler):
    server_version = "ITECHBench/1.0"

    def log_message(self, fmt, *args):  # 静音访问日志
        pass

    # ---------- 工具 ----------
    def _json(self, obj, code=200):
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n).decode("utf-8"))
        except Exception:  # noqa: BLE001
            return {}

    # ---------- GET ----------
    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/api/status":
            return self._json(BENCH.snapshot())
        if path == "/api/ports":
            return self._json({"ports": list_serial_ports(), "has_serial": HAS_SERIAL})
        if path == "/api/fs/list":
            qs = parse_qs(urlparse(self.path).query)
            p = (qs.get("path") or [""])[0].strip()
            try:
                return self._json(list_dirs(p))
            except OSError as exc:
                return self._json({"error": str(exc)}, 400)
        if path == "/api/export.csv":
            qs = parse_qs(urlparse(self.path).query)
            role = (qs.get("role") or ["sweep"])[0]
            try:
                with BENCH.lock:
                    name, text = BENCH.export(role)
            except InstrumentError as exc:
                return self._json({"error": str(exc)}, 400)
            data = text.encode("utf-8-sig")   # BOM: Excel 直接双击可正确识别中文
            self.send_response(200)
            self.send_header("Content-Type", "text/csv; charset=utf-8")
            self.send_header("Content-Disposition", f'attachment; filename="{quote(name)}"')
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        return self._static(path)

    def _static(self, path: str):
        if path in ("/", ""):
            path = "/index.html"
        target = (WEB_DIR / path.lstrip("/")).resolve()
        if not str(target).startswith(str(WEB_DIR.resolve())) or not target.is_file():
            self.send_error(404)
            return
        data = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", MIME.get(target.suffix.lower(), "application/octet-stream"))
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    # ---------- POST ----------
    def do_POST(self):
        path = urlparse(self.path).path
        body = self._body()
        try:
            if path == "/api/connect":
                return self._json(
                    BENCH.connect(
                        body["dev"],
                        body.get("port", ""),
                        int(body.get("baud", 9600)),
                        bool(body.get("sim", False)),
                        bool(body.get("safe_start", True)),
                        body.get("model"),
                    )
                )
            if path == "/api/disconnect":
                return self._json(BENCH.disconnect(body["dev"]))
            if path == "/api/power/set":
                return self._json(
                    BENCH.power_set(
                        body["dev"],
                        int(body["ch"]),
                        body.get("v"),
                        body.get("i"),
                    )
                )
            if path == "/api/power/output":
                return self._json(BENCH.power_output(body["dev"], body.get("ch"), bool(body["on"])))
            if path == "/api/load/set":
                return self._json(BENCH.load_set(body["dev"], body["mode"], float(body["value"])))
            if path == "/api/load/input":
                return self._json(BENCH.load_input(body["dev"], bool(body["on"])))
            if path == "/api/estop":
                return self._json(BENCH.estop())
            if path == "/api/instrument/add":
                return self._json(BENCH.add_instrument(body.get("role", ""), body.get("model")))
            if path == "/api/instrument/remove":
                return self._json(BENCH.remove_instrument(body.get("dev", "")))
            if path == "/api/instrument/reorder":
                return self._json(BENCH.reorder_instruments(body.get("ids")))
            if path == "/api/limits":
                with BENCH.lock:
                    for k in ("max_v", "max_i", "max_p"):
                        if k in body:
                            BENCH.limits[k] = float(body[k])
                    if "auto_trip" in body:
                        BENCH.limits["auto_trip"] = bool(body["auto_trip"])
                return self._json(BENCH.snapshot())
            if path == "/api/sweep/start":
                return self._json(BENCH.start_sweep(body))
            if path == "/api/sweep/stop":
                return self._json(BENCH.stop_sweep())
            if path == "/api/history/clear":
                with BENCH.lock:
                    BENCH.history.clear()
                return self._json(BENCH.snapshot())
            if path == "/api/selftest":
                return self._json(BENCH.selftest(body.get("dev", "")))
            if path == "/api/record/start":
                return self._json(
                    BENCH.record_start(body.get("dev", ""), body.get("channels"),
                                       body.get("path", ""), body.get("outputs"),
                                       body.get("interval"))
                )
            if path == "/api/record/stop":
                return self._json(BENCH.record_stop(body["id"]))
            if path == "/api/fs/mkdir":
                target = Path(body.get("path", ""))
                if not str(target).strip():
                    return self._json({"error": "路径为空"}, 400)
                target.mkdir(parents=True, exist_ok=True)
                return self._json({"ok": True, "path": str(target)})
            self.send_error(404)
        except InstrumentError as exc:
            self._json({"error": str(exc)}, 400)
        except KeyError as exc:
            self._json({"error": f"缺少参数: {exc}"}, 400)
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            self._json({"error": f"内部错误: {exc}"}, 500)


def build_server(host: str = "127.0.0.1", port: int = 0) -> ThreadingHTTPServer:
    """创建本地服务（port=0 表示由系统分配空闲端口）。"""
    srv = ThreadingHTTPServer((host, port), Handler)
    srv.daemon_threads = True
    return srv


def shutdown() -> None:
    """退出前关断所有输出并释放串口。"""
    try:
        BENCH.estop()
    except Exception:  # noqa: BLE001
        pass
    for dev_id in list(BENCH.devices.keys()):
        try:
            BENCH.disconnect(dev_id)
        except Exception:  # noqa: BLE001
            pass


def main() -> None:
    """浏览器模式（开发/调试用）；桌面模式请运行 desktop.py。"""
    ap = argparse.ArgumentParser(description="ITECH IT6302 + IT8511A+ 上位机")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()

    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass

    srv = build_server(args.host, args.port)
    url = f"http://{args.host}:{srv.server_address[1]}/"
    print("=" * 56)
    print("  ITECH 上位机  |  IT6302 电源 + IT8511A+ 电子负载")
    print("=" * 56)
    print(f"  界面地址: {url}")
    print(f"  pyserial: {'已安装' if HAS_SERIAL else '未安装 (仅模拟模式)'}")
    print("  按 Ctrl+C 退出")
    print("=" * 56)
    if not args.no_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n正在退出, 关闭仪器输出…")
    finally:
        shutdown()
        srv.server_close()


if __name__ == "__main__":
    main()
