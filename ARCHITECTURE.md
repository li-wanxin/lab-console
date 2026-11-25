# 架构说明与扩展指南

> 面向后续维护/AI 接手。改动前先读本文, 可以少走弯路。

## 模块结构

```
app.py          业务层 + HTTP 服务 (Bench: 设备/轮询/记录/扫描/更新检查)
instruments.py  仪器驱动层 (传输 + 各型号驱动 + MODELS 统一注册表 + 模拟器)
recording.py    记录导出 (xlsx 三页: 概览/数据/曲线图, 双轴图表)
sysenv.py       跨平台差异层 (数据目录/打开文件/根目录枚举 —— Mac 移植只改这里)
desktop.py      pywebview 桌面壳
web/            前端 (原生 JS, 面板按角色注册)
```

依赖方向: `app.py → recording.py / instruments.py / sysenv.py`。
驱动层与 UI 层互不引用; 前后端只通过 `/api/status` 与 `/api/*` 通信。

## 新增一台仪器 (后端)

在 `instruments.py` 的 `MODELS` 注册表加一个条目即可, 其余全部自动跟随
(添加仪器对话框、波特率下拉、量程校验、连接逻辑、模拟模式):

```python
MODELS["load"]["IT8512"] = {
    "driver": IT8512Driver,        # 必须提供 classmethod open(port, baud)
    "sim": SimLoad,                # 模拟器类 (负载类接受 model= 关键字)
    "label": "可编程直流电子负载 (…)",
    "channels": 1,                 # 电源填通道数, 负载固定 1
    "baud_rates": [9600], "baud_default": 9600,
    "ranges": {"max_v": …, "max_i": …, "max_p": …, "max_r": …},   # 负载需要
}
```

驱动类约定 (与既有 IT6302 / IT8510Legacy 同签名):

| 角色 | 必须实现 | 说明 |
|---|---|---|
| 电源 | `measure(ch) -> (v, i)`、`set_voltage/set_current/set_output`、`query_setpoints`、`raw_query`、`local`、`close`、`idn` | 构造收 `SerialTransport` |
| 负载 | `measure() -> (v, i, p)`、`set_mode/set_level/set_input`、`raw_query`、`local`、`close`、`idn` | 构造收 `(port, baud)` 自管串口 |

协议差异用 `ScpiTransportMixin` / `LegacyTransportMixin` 的 `open()` 吸收,
`Bench.connect()` 对所有型号走同一句 `entry["driver"].open(port, baud)`。

**新角色** (如万用表/示波器, 非 power/load): 除注册 MODELS 外, 还需
① `Dev`/`Bench` 的采样与快照分支; ② `recording.rec_columns` 补列定义;
③ 前端 `ROLE_PANELS` 注册面板三件套。当前三种角色的分支即模板。

### 非串口传输 (USB USBTMC, 以示波器为例)

示波器 `scope` 角色是本项目第一台**不走串口**的仪器, 已落地的做法可直接照搬:

- `MODELS["scope"]["DSOX1102G"]` 带 `"transport": "usbtmc"`, `baud_rates: [0]`;
- 传输层 `UsbTmcTransport` 与 `SerialTransport` 同构 (`write/query/query_raw/close`),
  驱动内部只认这个接口 → 换传输不影响业务层;
- `open(port, baud)` 里 `port` 复用为 `"USB"` / `"2A8D:1797"` (VID:PID), 波特率忽略;
- **Windows 前置条件**: 1000X 系列只有 USB 设备口, 系统不带 USBTMC 驱动,
  必须把设备接口绑定 WinUSB (`Zadig` 一次性操作)。绑 WinUSB 与 Keysight
  IO Libraries 互斥 —— 装了官方库的机器上 libusb 打不开设备, 二者只能选一。
- 波形读取: `:WAVeform:FORMat BYTE` + `POINts` + `:WAVeform:DATA?` (IEEE488.2 块),
  电压 = `(原始字节 - yreference) * yincrement + yorigin` (**漏掉 yreference 会整体偏移**)。
- REQUEST_DEV_DEP_MSG_IN 的 TransferSize 用 64KB; 请求 1MB 会被设备当作 0 长度空响应。
- 降采样必须按桶取 min/max 包络, 简单抽点会让高频噪声产生混叠假象。

## 升级路径 (v0.0.8 起)

- 安装版 = **onedir** 目录式打包 (`dist\LabConsole\` → `{app}`), 启动不再解压临时目录,
  避免单文件包首次启动被杀软首扫打断 ("Failed to load Python DLL")。
- 便携版仍为 onefile 单文件。
- PyInstaller 打包示波器功能需带 `--collect-all libusb_package` (内置 libusb-1.0.dll)。

## 新增仪器 (前端)

`web/app.js` 顶部 `ROLE_PANELS` 注册表: `{html, render, curves}` 三件套。
同角色不同型号自动复用同一面板 (量程来自 `/api/status` 的 model_specs/range)。

## 记录导出 (recording.py)

- 列定义集中在 `rec_columns(rec)`, 统计表/曲线图按列定义自动适配;
- 图表绘制的 5 条经验写在模块 docstring, **勿回退** (Excel 兼容性踩坑所得);
- 新角色记录: 在 `rec_columns` 加分支 + rec 里带上 outputs/通道结构即可。

## Mac 移植 (详见 PORTING.md)

平台差异全部收敛在 `sysenv.py`; 构建用 `build.sh`。产品代码目前
**没有** Windows 专属 API (`os.startfile`/`APPDATA`/盘符枚举已全部收编)。
