#!/usr/bin/env python3
"""精简 USBTMC (USB488) 传输实现，只依赖 pyusb + libusb-package。

DSOX1102G 走 USB 时需要设备接口已绑定 WinUSB (Zadig 一次性操作)。

协议要点 (USBTMC 1.0):
  写: Bulk OUT, 12 字节头 + 数据
      MsgID(1)=1 bTag bTagInverse 0 TransferSize(LE4) bmTransferAttributes(1) 0 0 0
  读: 先写 REQUEST_DEV_DEP_MSG_IN(MsgID=2), 再从 Bulk IN 读头(12B)+数据,
      直到 bmTransferAttributes 的 EOM(bit0) 置位
"""
from __future__ import annotations

import struct
import time

import libusb_package
import usb.backend.libusb1
import usb.core
import usb.util


class UsbTmcError(RuntimeError):
    pass


class UsbTmc:
    """单个 USBTMC 设备会话 (一次 open → 多次 query → close)。"""

    HEADER_FMT = "<BBBBI Bxxx"      # 12 字节: MsgID/tag/tagInv/rsv/size/flags/rsv3
    MSG_DEV_DEP_OUT = 1
    MSG_DEV_DEP_IN = 2
    FLAG_EOM = 0x01
    FLAG_TERM = 0x02

    def __init__(self, vid: int, pid: int, timeout_ms: int = 5000):
        backend = usb.backend.libusb1.get_backend(
            find_library=libusb_package.find_library)
        dev = usb.core.find(idVendor=vid, idProduct=pid, backend=backend)
        if dev is None:
            raise UsbTmcError("未找到设备 (检查 USB 连接与 WinUSB 驱动绑定)")
        self.dev = dev
        self.timeout = timeout_ms
        self.tag = 0
        self.serial = self._safe(lambda: dev.serial_number)

        try:
            dev.set_configuration()
        except usb.core.USBError as exc:
            # 已被配置过 (Resource busy) 属正常, 忽略
            if "busy" not in str(exc).lower() and "errno 16" not in str(exc).lower():
                raise

        cfg = dev.get_active_configuration()
        self.itf = cfg[(0, 0)]
        # 端点: BulkOUT / BulkIN <512~>, 以及可选 Interrupt IN
        self.ep_out = self.ep_in = None
        for ep in self.itf:
            if ep.bmAttributes & 0x03 == 0x02:
                if ep.bEndpointAddress & 0x80:
                    self.ep_in = ep
                else:
                    self.ep_out = ep
        if not (self.ep_out and self.ep_in):
            raise UsbTmcError("未找到 USBTMC Bulk 端点")
        self._clear_halts()

    # ---------- 基础工具 ----------
    @staticmethod
    def _safe(fn, default=None):
        try:
            return fn()
        except Exception:  # noqa: BLE001
            return default

    def _next_tag(self) -> int:
        self.tag = self.tag % 255 + 1
        return self.tag

    def _clear_halts(self) -> None:
        for ep in (self.ep_out, self.ep_in):
            try:
                self.dev.clear_halt(ep.bEndpointAddress)
            except Exception:  # noqa: BLE001
                pass

    def _pack(self, msg_id: int, size: int, flags: int) -> bytes:
        tag = self._next_tag()
        return struct.pack(self.HEADER_FMT, msg_id, tag, (~tag) & 0xFF,
                           0, size, flags)

    # ---------- 写 ----------
    def write(self, data: bytes | str, eom: bool = True) -> None:
        if isinstance(data, str):
            data = data.encode("ascii", "replace")
        if not data.endswith(b"\n"):
            data += b"\n"
        # TransferSize 必须是消息实际长度 (不含 4 字节对齐填充),
        # 否则设备会把填充的 0x00 当成命令内容
        flags = self.FLAG_EOM if eom else 0
        header = self._pack(self.MSG_DEV_DEP_OUT, len(data), flags)
        pad = (-len(data)) % 4
        self.dev.write(self.ep_out.bEndpointAddress,
                       header + data + b"\x00" * pad, self.timeout)

    # ---------- 读 ----------
    def read_raw(self, chunk_bytes: int = 1 << 16) -> bytes:
        """按 USBTMC 规范分块读取响应, 直到 EOM。

        每个分块都要先发 REQUEST_DEV_DEP_MSG_IN; 请求的 TransferSize
        不能超过设备缓冲上限 (DSOX1102G 用 64KB 稳妥, 1MB 会被当成
        0 长度空响应)。
        """
        chunks: list[bytes] = []
        for _ in range(256):                       # 防御性上限 (16MB)
            req = self._pack(self.MSG_DEV_DEP_IN, chunk_bytes, 0)
            self.dev.write(self.ep_out.bEndpointAddress, req, self.timeout)

            hdr = bytes(self.dev.read(self.ep_in.bEndpointAddress, 12,
                                      self.timeout))
            if len(hdr) < 12:
                raise UsbTmcError(f"响应头不完整: {len(hdr)} 字节")
            _msg, _tag, _tagi, _rsv, size, flags = \
                struct.unpack(self.HEADER_FMT, hdr)
            body = b""
            remain = size
            while remain > 0:
                got = bytes(self.dev.read(self.ep_in.bEndpointAddress,
                                          min(remain, 1 << 16), self.timeout))
                if not got:
                    break
                body += got
                remain -= len(got)
            chunks.append(body)
            if flags & (self.FLAG_EOM | self.FLAG_TERM):
                break
        return b"".join(chunks)

    def query(self, cmd: str, delay: float = 0.0) -> str:
        self.write(cmd)
        if delay:
            time.sleep(delay)
        # 有些命令 (如 *OPC?) 需要稍等设备准备
        for attempt in range(3):
            try:
                return self.read_raw().decode("ascii", "replace").strip()
            except usb.core.USBTimeoutError:
                if attempt == 2:
                    raise UsbTmcError(f"读取超时: {cmd}")
                time.sleep(0.2)
        return ""

    def query_raw(self, cmd: str) -> bytes:
        """二进制块查询 (:WAVeform:DATA? 等)。"""
        self.write(cmd)
        return self.read_raw()

    def close(self) -> None:
        try:
            usb.util.dispose_resources(self.dev)
        except Exception:  # noqa: BLE001
            pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
