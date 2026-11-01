"""
ITECH 仪器驱动层
================
- IT6302   三路可编程直流电源 (CH1/CH2: 30V/3A, CH3: 5V/3A)
- IT8511A+ 可编程直流电子负载 (150V/30A/150W)
- IT8510   可编程直流电子负载 (120V/30A/300W)

通信: 两类仪器后面板 DB9 均为 TTL 电平, 需 IT-E121(RS232)/IT-E122(USB) 转换模块,
      在 PC 上表现为标准串口 (COMx)。SCPI 命令, 8N1, 默认 9600bps, 以 \\n 结束。

命令依据:
  IT6302 Programming Guide  (SYST:REM / INST CHx / VOLT / CURR / CHAN:OUTP / MEAS:VOLT? / MEAS:CURR?)
  IT8500+ Programming Guide (SYST:REM / FUNC / CURR / VOLT / RES / POW / INP / MEAS:VOLT? / MEAS:CURR? / MEAS:POW?)
  IT8510 属 IT8500 系列, 协议命令集与 IT8511A+ 完全同族, 仅量程不同。
"""

from __future__ import annotations

import random
import re
import threading
import time

try:
    import serial
    from serial.tools import list_ports

    HAS_SERIAL = True
except ImportError:  # 没装 pyserial 时仍可用模拟模式
    HAS_SERIAL = False

_NUM = re.compile(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?")


class InstrumentError(RuntimeError):
    pass


def parse_float(text: str) -> float:
    m = _NUM.search(text or "")
    if not m:
        raise InstrumentError(f"无法解析仪器返回值: {text!r}")
    return float(m.group())


def list_serial_ports() -> list[dict]:
    if not HAS_SERIAL:
        return []
    out = []
    for p in list_ports.comports():
        out.append({"device": p.device, "description": p.description or "", "hwid": p.hwid or ""})
    out.sort(key=lambda x: x["device"])
    return out


# --------------------------------------------------------------------------- #
# 传输层
# --------------------------------------------------------------------------- #
class SerialTransport:
    """一条串口连接。所有收发串行化, 避免轮询线程与命令线程互相踩。"""

    def __init__(self, port: str, baud: int = 9600, timeout: float = 1.0):
        if not HAS_SERIAL:
            raise InstrumentError("未安装 pyserial, 无法使用真实串口 (可先用模拟模式)")
        self.port = port
        self.lock = threading.RLock()
        try:
            self.ser = serial.Serial(
                port=port,
                baudrate=baud,
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                timeout=timeout,
                write_timeout=timeout,
            )
        except Exception as exc:  # noqa: BLE001
            raise InstrumentError(f"打开串口 {port} 失败: {exc}") from exc
        time.sleep(0.1)
        try:
            self.ser.reset_input_buffer()
            self.ser.reset_output_buffer()
        except Exception:  # noqa: BLE001
            pass

    def write(self, cmd: str) -> None:
        with self.lock:
            try:
                self.ser.reset_input_buffer()
                self.ser.write((cmd + "\n").encode("ascii", "ignore"))
                self.ser.flush()
            except Exception as exc:  # noqa: BLE001
                raise InstrumentError(f"发送失败 [{cmd}]: {exc}") from exc

    def query(self, cmd: str) -> str:
        with self.lock:
            self.write(cmd)
            try:
                raw = self.ser.readline()
            except Exception as exc:  # noqa: BLE001
                raise InstrumentError(f"读取失败 [{cmd}]: {exc}") from exc
            if not raw:
                raise InstrumentError(f"仪器无响应 (超时): {cmd}")
            text = raw.decode("ascii", "ignore").strip()
            if not text:
                raise InstrumentError(f"仪器返回空响应: {cmd}")
            return text

    def close(self) -> None:
        with self.lock:
            try:
                self.ser.close()
            except Exception:  # noqa: BLE001
                pass


# --------------------------------------------------------------------------- #
# 真实仪器
# --------------------------------------------------------------------------- #
class IT6302:
    model = "IT6302"
    MAX_V = {1: 30.0, 2: 30.0, 3: 5.0}
    MAX_I = 3.0
    MAX_P = {1: 90.0, 2: 90.0, 3: 15.0}

    def __init__(self, transport: SerialTransport):
        self.t = transport
        self._idn = self.t.query("*IDN?")
        self.t.write("SYST:REM")

    @property
    def idn(self) -> str:
        return self._idn

    def select(self, ch: int) -> None:
        if ch not in (1, 2, 3):
            raise InstrumentError(f"非法通道: {ch}")
        self.t.write(f"INST CH{ch}")

    def set_voltage(self, ch: int, volts: float) -> None:
        self.select(ch)
        self.t.write(f"VOLT {volts:.3f}")

    def set_current(self, ch: int, amps: float) -> None:
        self.select(ch)
        self.t.write(f"CURR {amps:.3f}")

    def set_output(self, ch: int, on: bool) -> None:
        """CHANnel:OUTPut 只作用于当前通道。"""
        self.select(ch)
        self.t.write(f"CHAN:OUTP {'ON' if on else 'OFF'}")

    def measure(self, ch: int) -> tuple[float, float]:
        self.select(ch)
        v = parse_float(self.t.query("MEAS:VOLT?"))
        i = parse_float(self.t.query("MEAS:CURR?"))
        return v, i

    def query_setpoints(self, ch: int) -> tuple[float, float]:
        self.select(ch)
        v = parse_float(self.t.query("VOLT?"))
        i = parse_float(self.t.query("CURR?"))
        return v, i

    def raw_query(self, cmd: str) -> str:
        return self.t.query(cmd)

    def local(self) -> None:
        self.t.write("SYST:LOC")

    def close(self) -> None:
        self.t.close()


class IT8511A:
    model = "IT8511A+"
    MAX_V = 150.0
    MAX_I = 30.0
    MAX_P = 150.0

    MODES = {"CURR": "CC", "VOLT": "CV", "RES": "CR", "POW": "CP"}
    SET_CMD = {"CC": "CURR", "CV": "VOLT", "CR": "RES", "CP": "POW"}

    def __init__(self, transport: SerialTransport):
        self.t = transport
        self._idn = self.t.query("*IDN?")
        self.t.write("SYST:REM")

    @property
    def idn(self) -> str:
        return self._idn

    def set_mode(self, mode: str) -> None:
        if mode not in self.SET_CMD:
            raise InstrumentError(f"非法模式: {mode}")
        self.t.write(f"FUNC {self.SET_CMD[mode]}")

    def set_level(self, mode: str, value: float) -> None:
        cmd = self.SET_CMD.get(mode)
        if not cmd:
            raise InstrumentError(f"非法模式: {mode}")
        self.t.write(f"{cmd} {value:.4f}")

    def set_input(self, on: bool) -> None:
        self.t.write(f"INP {'ON' if on else 'OFF'}")

    def measure(self) -> tuple[float, float, float]:
        v = parse_float(self.t.query("MEAS:VOLT?"))
        i = parse_float(self.t.query("MEAS:CURR?"))
        p = parse_float(self.t.query("MEAS:POW?"))
        return v, i, p

    def raw_query(self, cmd: str) -> str:
        return self.t.query(cmd)

    def local(self) -> None:
        self.t.write("SYST:LOC")

    def close(self) -> None:
        self.t.close()


class IT8510(IT8511A):
    """IT8510 电子负载: IT8500 系列, SCPI 协议与 IT8511A+ 同族, 仅量程不同。"""

    model = "IT8510"
    MAX_V = 120.0
    MAX_I = 30.0
    MAX_P = 300.0


# 型号注册表: 前端"添加仪器"对话框与后端量程校验共用
LOAD_MODEL_SPECS = {
    "IT8511A+": {"max_v": 150.0, "max_i": 30.0, "max_p": 150.0, "max_r": 7500.0},
    "IT8510": {"max_v": 120.0, "max_i": 30.0, "max_p": 300.0, "max_r": 7500.0},
}
LOAD_DRIVERS = {
    "IT8511A+": IT8511A,
    "IT8510": IT8510,
}
POWER_MODEL_SPECS = {
    "IT6302": {"channels": 3},
}
MODEL_CATALOG = {
    "power": ["IT6302"],
    "load": ["IT8511A+", "IT8510"],
}


# --------------------------------------------------------------------------- #
# 模拟器 (无硬件时可用, 两台设备联动, 便于先把界面/流程跑通)
# --------------------------------------------------------------------------- #
class SimPower:
    model = "IT6302 (模拟)"
    MAX_V = {1: 30.0, 2: 30.0, 3: 5.0}
    MAX_I = 3.0
    MAX_P = {1: 90.0, 2: 90.0, 3: 15.0}

    def __init__(self, load: "SimLoad | None" = None):
        self.load = load
        self._idn = "ITECH,IT6302,SIMULATED,1.0"
        self.set_v = {1: 0.0, 2: 0.0, 3: 0.0}
        self.set_i = {1: 0.1, 2: 0.1, 3: 0.1}
        self.on = {1: False, 2: False, 3: False}
        self._rng = random.Random(7)

    @property
    def idn(self) -> str:
        return self._idn

    def select(self, ch: int) -> None:
        """模拟器无通道上下文, 保留接口一致性。"""

    def set_voltage(self, ch: int, volts: float) -> None:
        self.set_v[ch] = max(0.0, min(volts, self.MAX_V[ch]))

    def set_current(self, ch: int, amps: float) -> None:
        self.set_i[ch] = max(0.001, min(amps, self.MAX_I))

    def set_output(self, ch: int, on: bool) -> None:
        self.on[ch] = bool(on)

    def measure(self, ch: int) -> tuple[float, float]:
        if not self.on[ch] or self.set_v[ch] <= 0:
            return 0.0, 0.0
        v = self.set_v[ch]
        # 通道 1 与负载联动: 负载按自身模式拉电流
        if ch == 1 and self.load is not None and self.load.on:
            i = self.load.current_for(v)
        else:
            i = min(self.set_i[ch], v / 100.0)  # 默认接了个 100Ω 假负载
        i = min(i, self.set_i[ch])
        # 输出内阻 + 线阻造成压降
        v_load = max(0.0, v - i * 0.06)
        v_meas = v_load * (1 + self._rng.uniform(-0.0008, 0.0008))
        i_meas = i * (1 + self._rng.uniform(-0.002, 0.002))
        return round(v_meas, 4), round(i_meas, 4)

    def query_setpoints(self, ch: int) -> tuple[float, float]:
        return self.set_v[ch], self.set_i[ch]

    def raw_query(self, cmd: str) -> str:
        return "SIMULATED"

    def local(self) -> None:
        pass

    def close(self) -> None:
        pass


class SimLoad:
    model = "IT8511A+ (模拟)"

    def __init__(self, power: "SimPower | None" = None, model: str = "IT8511A+"):
        spec = LOAD_MODEL_SPECS.get(model) or LOAD_MODEL_SPECS["IT8511A+"]
        self.model = f"{model} (模拟)"
        self.model_id = model
        self.MAX_V = spec["max_v"]
        self.MAX_I = spec["max_i"]
        self.MAX_P = spec["max_p"]
        self.power = power
        self._idn = f"ITECH,{model},SIMULATED,1.0"
        self.mode = "CC"
        self.level = 0.5
        self.on = False
        self._rng = random.Random(11)
        self._v = 0.0

    @property
    def idn(self) -> str:
        return self._idn

    def current_for(self, v: float) -> float:
        if not self.on or v <= 0:
            return 0.0
        if self.mode == "CC":
            i = self.level
        elif self.mode == "CV":
            i = 30.0 if v < self.level else 0.0
        elif self.mode == "CR":
            i = v / max(self.level, 0.01)
        else:  # CP
            i = self.level / max(v, 0.1)
        # 功率与电流上限
        i = min(i, self.MAX_I, self.MAX_P / max(v, 0.1))
        return max(0.0, i)

    def set_mode(self, mode: str) -> None:
        self.mode = mode

    def set_level(self, mode: str, value: float) -> None:
        self.mode = mode
        self.level = max(0.0, value)

    def set_input(self, on: bool) -> None:
        self.on = bool(on)

    def raw_query(self, cmd: str) -> str:
        return "SIMULATED"

    def measure(self) -> tuple[float, float, float]:
        if not self.on:
            return 0.0, 0.0, 0.0
        if self.power is not None:
            v, _ = self.power.measure(1)
            if not self.power.on[1]:
                v = 0.0
        else:
            v = 0.0
        i = self.current_for(v)
        v_meas = v * (1 + self._rng.uniform(-0.0008, 0.0008))
        i_meas = i * (1 + self._rng.uniform(-0.002, 0.002))
        return round(v_meas, 4), round(i_meas, 4), round(v_meas * i_meas, 4)

    def local(self) -> None:
        pass

    def close(self) -> None:
        pass
