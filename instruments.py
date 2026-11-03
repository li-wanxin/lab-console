"""
ITECH 仪器驱动层
================
- IT6302   三路可编程直流电源 (CH1/CH2: 30V/3A, CH3: 5V/3A)
- IT8511A+ 可编程直流电子负载 (150V/30A/150W, SCPI)
- IT8510   可编程直流电子负载 (120V/20A/120W, 老款二进制协议)

通信: 两类仪器后面板 DB9 均为 TTL 电平, 需 IT-E121(RS232)/IT-E122(USB) 转换模块,
      在 PC 上表现为标准串口 (COMx)。
  IT6302 / IT8511A+: SCPI 文本命令, 8N1, 默认 9600bps, 以 \n 结束。
  IT8510 (老款, 非"+"机型): 26 字节二进制私有协议, 8N1, 默认 4800bps。
      2026-11-02 真机实测 (S/N 3797370020): 不响应任何波特率下的 SCPI;
      帧格式 AA <addr> <cmd> <payload> <cksum>, 校验和 = 前 25 字节累加模 256;
      设置类应答 AA <addr> 0x12 <status>..., 0x80=OK / 0xA0=参数超范围 /
      0xB0=无法执行(常见于未进远程模式); 查询类应答原样回显 cmd 字节。
      量程 (官方规格书 + 真机拒绝法验证): 120V/20A/120W(保护设定上限 130W),
      CR 最高 4000Ω, CC 分辨率 0.1mA。

命令依据:
  IT6302 Programming Guide  (SYST:REM / INST CHx / VOLT / CURR / CHAN:OUTP / MEAS:VOLT? / MEAS:CURR?)
  IT8500+ Programming Guide (SYST:REM / FUNC / CURR / VOLT / RES / POW / INP / MEAS:VOLT? / MEAS:CURR? / MEAS:POW?)
  IT8510 老款协议: 参照 Blinkinlabs/itech_usb_serial (it8500.py) 命令表, 关键命令已真机逐一验证
  (0x20 远程/本地, 0x21 输入, 0x22~0x27 保护限, 0x28/0x29 模式, 0x2A~0x31 四模式电平,
   0x5F 测量(V mV/I 0.1mA/P mW/op_state), 0x6A 识别)
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
    MODES = {"CURR": "CC", "VOLT": "CV", "RES": "CR", "POW": "CW"}
    SET_CMD = {"CC": "CURR", "CV": "VOLT", "CR": "RES", "CW": "POW"}

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


# IT8510 老款机型 (非"+"): 不讲 SCPI, 用 26 字节二进制私有协议, 默认 4800bps。
class IT8510Legacy:
    """IT8510 老款电子负载, 26 字节二进制协议驱动 (2026-11-02 真机验证通过)。

    状态码: 0x80=OK, 0x90=校验和错, 0xA0=参数超范围, 0xB0=无法执行, 0xC0=非法命令。
    量程 (官方规格书 + 真机拒绝法验证): 120V/20A/120W (保护设定上限 130W), CR 4000Ω, CC 0.1mA 步进。
    """

    LEGACY_SERIAL = True  # app.py 据此用 (port, baud) 而非 SerialTransport 构造
    model = "IT8510"
    MAX_V = 120.0
    MAX_I = 20.0
    MAX_P = 120.0

    # 模式名沿用 IT8500+ 面板叫法: CW = 恒功率 (Constant Wattage, 老手册写作 CP);
    # 帧协议命令码不变 (CW 档 = 0x02, 电平命令 0x2E/0x2F)。
    _MODE_CODE = {"CC": 0, "CV": 1, "CW": 2, "CR": 3}
    _MODE_NAME = {0: "CC", 1: "CV", 2: "CW", 3: "CR"}
    _LEVEL_SET = {"CC": 0x2A, "CV": 0x2C, "CW": 0x2E, "CR": 0x30}
    _LEVEL_GET = {"CC": 0x2B, "CV": 0x2D, "CW": 0x2F, "CR": 0x31}
    _LEVEL_SCALE = {"CC": 10000.0, "CV": 1000.0, "CW": 1000.0, "CR": 1000.0}

    _STATUS = {0x80: "OK", 0x90: "校验和错误", 0xA0: "参数超出仪器范围",
               0xB0: "命令无法执行", 0xC0: "非法命令"}

    def __init__(self, port: str, baud: int = 4800, timeout: float = 1.2):
        if not HAS_SERIAL:
            raise InstrumentError("未安装 pyserial, 无法使用真实串口 (可先用模拟模式)")
        if not baud:
            baud = 4800
        self.lock = threading.RLock()
        try:
            self.ser = serial.Serial(port=port, baudrate=baud,
                                     bytesize=serial.EIGHTBITS,
                                     parity=serial.PARITY_NONE,
                                     stopbits=serial.STOPBITS_ONE,
                                     timeout=timeout, write_timeout=timeout)
        except Exception as exc:  # noqa: BLE001
            raise InstrumentError(f"打开串口 {port} 失败: {exc}") from exc
        self._idn = ""
        try:
            self._send_raw(0x20, b"\x01")  # 远程模式 (未进远程时设置命令回 0xB0)
            ident = self._exchange(0x6A)
        except InstrumentError:
            self.close()
            raise
        model = ident[3:8].decode("ascii", "ignore").split("\x00")[0]
        sn = ident[10:20].decode("ascii", "ignore").strip("\x00 ")
        fw = f"{ident[9]:02x}.{ident[8]:02x}"
        self._idn = f"ITECH,IT{model},SN:{sn},FW:{fw}"

    # ---- 帧层 ----
    @staticmethod
    def _frame(cmd: int, payload: bytes = b"", addr: int = 0) -> bytes:
        f = bytearray([0xAA, addr, cmd])
        f.extend(payload)
        f.extend(bytes(26 - 1 - len(f)))
        f.append(sum(f) % 256)
        return bytes(f)

    def _exchange(self, cmd: int, payload: bytes = b"") -> bytearray:
        """发送一帧并接收 26 字节应答; 校验头/回显/校验和。

        应答有两种形态 (2026-11-02 真机确认):
          设置类 ACK: AA <addr> 0x12 <status> ...  (不回显原命令码)
          查询类响应: AA <addr> <cmd> <data> ...   (回显原命令码)
        """
        with self.lock:
            try:
                self.ser.reset_input_buffer()
                self.ser.write(self._frame(cmd, payload))
                self.ser.flush()
                raw = self.ser.read(26)
            except Exception as exc:  # noqa: BLE001
                raise InstrumentError(f"串口收发失败 [{cmd:#04x}]: {exc}") from exc
            if len(raw) != 26 or raw[0] != 0xAA:
                got = raw.hex() if raw else "(空)"
                raise InstrumentError(
                    f"仪器无有效应答 [{cmd:#04x}] (got {got})。"
                    f"IT8510 老款默认 4800bps, 请核对波特率/接线/地址")
            if raw[2] != cmd and not (raw[2] == 0x12 and len(payload) > 0):
                raise InstrumentError(
                    f"应答命令码不符 [{cmd:#04x}]: got {raw[2]:#04x}")
            if (sum(raw[:25]) % 256) != raw[25]:
                raise InstrumentError(f"应答校验和错误 [{cmd:#04x}]: {raw.hex()}")
            return raw

    def _send_raw(self, cmd: int, payload: bytes = b"") -> None:
        """设置类命令: 校验 ACK 状态码。"""
        r = self._exchange(cmd, payload)
        status = r[3]
        if status != 0x80:
            raise InstrumentError(
                f"仪器拒绝 [{cmd:#04x}]: {self._STATUS.get(status, hex(status))}")

    def _get_int(self, cmd: int, n: int = 4) -> int:
        r = self._exchange(cmd)
        return int.from_bytes(r[3:3 + n], "little")

    # ---- 对外接口 (与 IT8511A 保持同签名) ----
    @property
    def idn(self) -> str:
        return self._idn

    def set_mode(self, mode: str) -> None:
        if mode not in self._MODE_CODE:
            raise InstrumentError(f"非法模式: {mode}")
        self._send_raw(0x28, bytes([self._MODE_CODE[mode]]))

    def set_level(self, mode: str, value: float) -> None:
        cmd = self._LEVEL_SET.get(mode)
        if not cmd:
            raise InstrumentError(f"非法模式: {mode}")
        raw = int(round(value * self._LEVEL_SCALE[mode]))
        self._send_raw(cmd, int(raw).to_bytes(4, "little"))

    def set_input(self, on: bool) -> None:
        self._send_raw(0x21, b"\x01" if on else b"\x00")

    def measure(self) -> tuple[float, float, float]:
        r = self._exchange(0x5F)
        v_mV, i_uA, p_mW = int.from_bytes(r[3:7], "little"), \
            int.from_bytes(r[7:11], "little"), int.from_bytes(r[11:15], "little")
        return v_mV / 1000.0, i_uA / 10000.0, p_mW / 1000.0

    def selftest_lines(self) -> list[str]:
        """老协议自检: 逐项查询, SCPI 标签 + 老协议命令码对照。"""
        out = [f"*IDN?(0x6A) → {self._idn}"]
        mode = self._MODE_NAME.get(self._get_int(0x29, 1), "?")
        out.append(f"FUNC?(0x29) → {mode}")
        out.append(f"CURR?(0x2B) → {self._get_int(0x2B) / 10000.0:.4f} A")
        out.append(f"maxV?(0x23) → {self._get_int(0x23) / 1000.0:.1f} V")
        out.append(f"maxI?(0x25) → {self._get_int(0x25) / 10000.0:.1f} A")
        out.append(f"maxP?(0x27) → {self._get_int(0x27) / 1000.0:.1f} W")
        v, i, p = self.measure()
        out.append(f"MEAS(0x5F) → V={v:.4f} I={i:.4f} P={p:.4f}")
        return out

    def raw_query(self, cmd: str) -> str:
        raise InstrumentError("IT8510(老款)为二进制协议, 不支持 SCPI 文本查询, 自检请用专用流程")

    def local(self) -> None:
        try:
            self._send_raw(0x20, b"\x00")  # 交还面板控制
        except Exception:  # noqa: BLE001
            pass

    def close(self) -> None:
        try:
            self.ser.close()
        except Exception:  # noqa: BLE001
            pass


class IT8511APlusLegacy(IT8510Legacy):
    """IT8511A+ 电子负载 —— 后面板 DB9 是 TTL 电平口, 必须通过
    IT-E121/E121A(RS-232) 或 IT-E122(USB) 通信模块连接电脑;
    直插标准 RS-232 线不仅不通, 官方手册警告可能损坏仪器。

    协议与 IT8510 老款同族 (IT8500 26 字节帧), 但默认 9600bps
    (2026-11-03 真机实测: 标识帧返回 8511A, SN 4747410185, FW 01.46;
    4800bps 无响应)。量程 150V/30A/150W, CR 最高 7500Ω。
    """

    model = "IT8511A+"
    MAX_V = 150.0
    MAX_I = 30.0
    MAX_P = 150.0


# 型号注册表: 前端"添加仪器"对话框与后端量程校验共用
# baud_rates: 该型号串口实际支持的波特率 (前端下拉据此渲染; 官方手册依据)
#   IT8510 老款: 仅 4800 (2026-11-02 真机实测其他速率无响应)
#   IT8511A+: 仅 9600 (2026-11-03 真机实测 4800/19200/38400 均无响应; SCPI
#             探测同样全波特率无响应, 该机串口仅支持帧协议)
#   IT6302: 4800/9600/38400 (用户手册 BAUD 菜单)
LOAD_MODEL_SPECS = {
    "IT8511A+": {"max_v": 150.0, "max_i": 30.0, "max_p": 150.0, "max_r": 7500.0,
                 "baud_rates": [9600], "baud_default": 9600},
    "IT8510": {"max_v": 120.0, "max_i": 20.0, "max_p": 120.0, "max_r": 4000.0,
               "baud_rates": [4800], "baud_default": 4800},
}
LOAD_DRIVERS = {
    # IT8511A+ 后面板 DB9 是 TTL 电平口, 必须经 IT-E121/E121A(RS232)/IT-E122(USB)
    # 通信模块连电脑, 协议与 IT8510 老款同族 (IT8500 帧), 但默认 9600bps ——
    # 不能用 SCPI 驱动 (真机 2026-11-03: 标识帧返回 8511A, SN 4747410185)
    "IT8511A+": IT8511APlusLegacy,
    "IT8510": IT8510Legacy,
}
POWER_MODEL_SPECS = {
    "IT6302": {"channels": 3, "baud_rates": [4800, 9600, 38400], "baud_default": 9600},
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
        else:  # CW 恒功率
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
