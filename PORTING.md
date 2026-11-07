# macOS 移植指南

目标: 在 Mac 上源码运行 / 打包出可分发的 Lab Console。
当前代码已为移植做好准备, 剩余工作集中在**打包形态**与**图标**。

## 已就绪 (无需改动)

| 项 | 现状 |
|---|---|
| 串口 | pyserial 跨平台; 端口枚举 `list_serial_ports()` 自动列 /dev/tty.* |
| 平台差异 | 全部收敛于 `sysenv.py` (数据目录 ~/Library/Application Support/LabConsole、open_external 用 `open`、根目录枚举返回 `/`) |
| 桌面壳 | pywebview 在 macOS 用 WKWebView, desktop.py 无平台分支 |
| HTTP/业务层 | 纯 Python 标准库 + openpyxl, 无平台依赖 |
| 自动更新 | 检测/下载/缓存全跨平台; "便携版运行中替换"在 POSIX 同样可行 (文件可即时改名) |

## 源码运行

```bash
python3 -m pip install -r requirements.txt
./build.sh run --port 8799 --no-browser
```

## 打包 (.app)

```bash
# 1. 图标: 由 app.ico 转出 macOS 专用 .icns (一次性)
#    最简做法: 在线/本机把 app.ico 转 app.icns, 或用 iconutil 从 iconset 生成
# 2. 打包
./build.sh pack          # 产出 dist/LabConsole.app
```

要点 (build.sh 已内置):
- macOS 窗口程序用 `--onedir --windowed`, **不要** `--onefile`(无 bundle、启动慢, PyInstaller 官方不建议);
- `--add-data` 分隔符在 POSIX 是 `:` (脚本自动处理);
- 图标用 `app.icns`; 没有时删掉 `--icon` 参数也能打, 只是默认图标。

## 打包后自查清单

- [ ] `dist/LabConsole.app` 双击启动, 窗口出现, 标题 Lab Console
- [ ] 添加仪器 → 模拟模式连接 → 曲线/记录/导出 xlsx 全流程
- [ ] 真实串口: 插上 USB 转换器, 端口下拉出现 `/dev/tty.usbserial-*`
- [ ] 检查更新: 手动点版本徽章, 能查到 GitHub 最新 Release
- [ ] 首次运行 Gatekeeper: 右键 → 打开 (未签名 app 的标准流程); 正式分发需 codesign + notarize

## 尚余的 Windows 专属项 (不影响 Mac 产品功能)

- `打包.bat` / `启动.bat` — Windows 脚本, Mac 用 `build.sh`;
- Inno Setup 安装包 — 仅 Windows 形态; Mac 分发用 .app (可后续加 dmg 压缩);
- `tools/shot.py` — 截图工具依赖 Win32, 仅开发辅助, 与产品无关;
- 更新分流: `update_download` 按 exe 名前缀 `labconsole` 判定安装版,
  Mac 的 .app 内可执行名注意保持 `LabConsole` (当前 --name 已满足)。
