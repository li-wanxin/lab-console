#!/usr/bin/env python3
"""DSOX1102G USB(USBTMC) 联通性与波形抓取探测脚本。

用法:
    python tools/scope_probe.py            # 只做连通性 + *IDN?
    python tools/scope_probe.py --wave     # 附带抓取 CH1 波形做验证

前提: 设备接口已由 Zadig 绑定 WinUSB。
"""
from __future__ import annotations

import sys
import time

import libusb_package
import usb.backend.libusb1
import usb.core

KEYSIGHT_VID = 0x2A8D
SCOPE_PID = 0x1797          # DSO-X 1102G


def find_scope():
    backend = usb.backend.libusb1.get_backend(
        find_library=libusb_package.find_library)
    dev = usb.core.find(idVendor=KEYSIGHT_VID, idProduct=SCOPE_PID,
                        backend=backend)
    if dev is None:
        raise SystemExit("未找到示波器: 检查 USB 线/Zadig 是否已绑 WinUSB")
    return dev


def open_scope(dev):
    import usbtmc
    inst = usbtmc.Instrument(dev)
    inst.timeout = 5000
    return inst


def probe_idn(inst):
    print("*IDN? ->", inst.ask("*IDN?"))
    print("固件/选件:", inst.ask(":SYSTem:OPTions?") if _safe(inst, ":SYSTem:OPTions?") else "n/a")


def _safe(inst, cmd: str) -> bool:
    try:
        inst.ask(cmd)
        return True
    except Exception:
        return False


def fetch_waveform(inst, ch: int = 1):
    """抓一帧 BYTE 波形, 返回 (时间数组, 电压数组, 元信息)。"""
    inst.write(f":WAVeform:SOURce CHANnel{ch}")
    inst.write(":WAVeform:FORMat BYTE")
    inst.write(":WAVeform:MODE NORMal")
    time.sleep(0.2)

    pre = inst.ask(":WAVeform:PREamble?")
    vals = [float(v) for v in pre.split(",")]
    # preamble: format,type,points,count,xincrement,xorigin,xreference,
    #           yincrement,yorigin,yreference
    xinc, xorig = vals[4], vals[5]
    yinc, yorig = vals[7], vals[8]
    npts = int(vals[2])

    raw = inst.ask_raw(b":WAVeform:DATA?")
    # 解析 IEEE 488.2 二进制块头: #<n><len><data>
    assert raw[:1] == b"#", f"非二进制块: {raw[:20]!r}"
    ndigits = int(raw[1:2])
    length = int(raw[2:2 + ndigits])
    data = raw[2 + ndigits:2 + ndigits + length]
    ys = [(b - yorig) * yinc for b in data]
    xs = [xorig + i * xinc for i in range(len(ys))]
    print(f"波形点数: {len(ys)} (preamble 声明 {npts}), "
          f"xinc={xinc:.3e}s/点, yinc={yinc:.3e}V/点")
    print(f"电压范围: {min(ys):.4f} ~ {max(ys):.4f} V, "
          f"时间跨度: {xs[-1] - xs[0]:.4e} s")
    return xs, ys, vals


def main() -> None:
    dev = find_scope()
    print(f"设备: bus={dev.bus} addr={dev.address} "
          f"sn={getattr(dev, 'serial_number', '?')}")
    with _ctx(open_scope(dev)) as inst:
        probe_idn(inst)
        for cmd in (":MEASure:VPP? CHANnel1", ":MEASure:FREQuency? CHANnel1",
                    ":MEASure:VAVerage? CHANnel1"):
            try:
                print(f"{cmd} -> {inst.ask(cmd).strip()}")
            except Exception as exc:  # noqa: BLE001
                print(f"{cmd} 失败: {exc}")
        if "--wave" in sys.argv:
            fetch_waveform(inst, 1)


class _ctx:
    """usbtmc.Instrument 支持上下文管理器? 统一兜底。"""

    def __init__(self, inst):
        self.inst = inst

    def __enter__(self):
        return self.inst

    def __exit__(self, *exc):
        try:
            self.inst.close()
        except Exception:  # noqa: BLE001
            pass


if __name__ == "__main__":
    main()
