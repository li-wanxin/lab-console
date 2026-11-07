# DEVLOG · 开发与变更记录

> **给接手者（人或 AI）的话**：本文档是项目的"进度记忆"。开始任何改动前，请先读完本文和 `README.md`；
> 完成改动后，请在「版本时间线」追加一行，并同步更新「当前状态」「待办」。
> 记录时写清：改了什么、为什么（用户需求原话可摘要）、怎么验证的。

---

## 一、项目快照

- **是什么**：实验室仪器控制台（上位机）。当前驱动 ITECH IT6302 三路电源、IT8511A+/IT8510 电子负载；架构支持注册更多型号，未来接其他厂家仪器。
- **技术栈**：Python 标准库 `ThreadingHTTPServer`（后端/轮询/扫描/记录） + `pyserial`（SCPI） + `pywebview`（桌面壳） + 原生 HTML/CSS/JS（前端，Canvas 手绘曲线，无任何前端框架）。
- **仓库**：`github.com/li-wanxin/lab-console`，日常开发在 **`dev`** 分支，`master` 留给稳定发布。
- **本地目录**：`C:/Users/liwanxin/WorkBuddy/2026-10-28-22-40-24/itech-bench/`（仓库名已定为 lab-console，目录改名待做，改名后需检查 启动.bat/打包.bat 的路径引用）。

## 二、运行与验证方法

```bash
python app.py --port 8799 --no-browser   # 开发预览（后台常驻）
python desktop.py                        # 桌面壳（pywebview 原生窗口）
```

- **截图验收**：`python tools/shot.py <url> <out.png> <宽> <高> [等待秒] [要执行的JS]`——固定尺寸无边框窗口截屏。截前先移开真实鼠标（`ctypes.windll.user32.SetCursorPos(1350,870)`），否则 hover 态会污染截图。
- **DOM 探针**：临时脚本用 `webview.create_window(..., hidden=True)` + `evaluate_js` 直接驱动界面（点按钮、读状态），适合自动化回归；同一会话内 `location.reload()` 可测 localStorage 恢复。
- **模拟模式**：无硬件时勾选"模拟模式"连接，电源带输出内阻模型、负载按 CC/CV/CR/CP 计算，可跑通全部交互。
- **界面版本号**：改了 web/ 下文件必须递增 `web/index.html` 里的 `?v=N`（防缓存）。当前 **v55**。

## 三、架构地图（改哪里找哪里）

| 文件 | 职责与关键点 |
|---|---|
| `app.py` | `Bench` 类：设备字典 `devices`（`Dev`：id=d1..、role、model、channels/state）；`poll_once()` 每 **0.8s** 轮询全部设备并写 history/recordings；扫描 `start_sweep`；记录 `record_start/record_stop`（`interval` 可配、`next_t` 调度）；estop/自动脱扣 `_safety_check`；所有 API 按 `dev` 寻址（`/api/power/set {dev,ch,v,i}` 等）。`POLL_INTERVAL=0.8`。注意 app.py 是 **LF** 行尾，批量替换前先确认 |
| `instruments.py` | 驱动层：`MODEL_CATALOG` 注册型号；IT6302（3 通道 SCPI）、IT8511A+/IT8510（IT8500+ 系列同协议）；`SimPSU/SimLoad` 模拟器。SCPI 均对照官方编程手册（见 README 第五节） |
| `web/index.html` | 静态骨架 + 对话框（`#wire-mask` 接线指引、`#add-mask` 添加仪器、`#rec-mask` 记录、`#combooff-mask` 组合占用提示）；`?v=N` 版本号 |
| `web/app.js` | 全部交互。全局状态 `S`：`S.view`（设备id）、`S.combo{}`（结构选择）、`S.comboLive{}`（**正按组合接通的组合键**）、`S.loadMode{}`、`S.axis`、`S.powerCells`。渲染：`renderPowerPanel`（卡片+占用角标）、`renderPowerCurves`（Canvas+数据表）。事件委托到 document（`data-act`/`data-out`/`data-set`/`data-wire-off`）。`logicalOutputs(devId)` 生成逻辑输出列表（单路3路 或 组合卡+剩余路） |

## 四、关键业务规则（勿破坏）

1. **组合占用/接通语义（v51-v52 重构）**：切换单路/组合视图**绝不改变输出状态**。`S.comboLive[devId]` 记录正按组合方式接通的组合键：组合卡 ON 要求 `comboLive === 当前结构`；单路视图下成员通道被接通组合占用 → 显示橙色「占用」+ OFF；尝试单独开启 → `#combooff-mask` 弹窗，确认后关闭组合输出**并继续按单路开启用户请求的通道**。组合接通须过接线指引（`#wire-mask`，警示条可逐通道手动关闭输出）。
2. **输出 ON 时修改设定（v54）**：先关闭该卡通道输出（组合卡=成员两路，清 comboLive）再写新值，toast 提示"输出已关闭，请确认后重新开启"。
3. **estop 是 POST**（曾有 GET 不生效的坑）；急停/全部 OFF/成员单独关闭 都要清 `comboLive` 并 `saveComboState()`。
4. **comboLive/combo 持久化**：localStorage `itech.combo` + `itech.combolive`，启动时恢复（校验键有效性）；主题 `itech.theme`、记录目录 `itech.recdir`、记录间隔 `itech.reciv`。
5. **设备命名**：删除后再添加，序号取**最大序号+1**（不是数量+1，曾修过此 bug）。
6. **样式变量**：`:root` 的暗色组件变量必须是**真实色值**，禁止 `--x: var(--x)` 自引用（循环引用会使背景透明，v54 修过）。浅色主题在 `html.light` 下整体覆盖。
7. **数据表默认 10 行**（223px = 表头23 + 10×20），`.cell-drag` 拖拽调高/低；表格行缓存 40。

## 五、版本时间线

- **2026-10 下旬（~v28-v40）**：项目成型。三通道曲线分条+游标、电压常用值弹层、轴设置、负载四模式、IT8510 支持、多设备添加/删除/排序、联动 I-V 扫描、CSV 导出、深浅主题。过程截图在 `preview/`。
- **2026-10-31（v41-v48）**：删除交互（悬停×→确认删除两步）、单路/组合独立段式按钮、记录功能（单路/多路、独立存储目录、时间列系统时钟）、目录系统选择、负载视图左侧栏修复、负载 UI 统一、提示词/图表字体放大、**多设备重构**（任意数量电源/负载，全 API 按 dev 寻址）、接线指引弹窗（端子竖放示意图）。
- **2026-10-31（v49）**：组合「占用≠接通」语义确立：成员独立输出时组合卡不显示 ON，显示占用角标；开启组合需过接线指引并可取消。
- **2026-11-01 上午（v50）**：接线指引警示条逐通道显示实时 V/A + 手动「关闭」按钮；本地同步 STATUS 免闪。
- **2026-11-01 下午（v51）**：**组合/单路切换状态语义重构**：`S.comboOn`→`S.comboLive`，切视图不动输出；单路下占用显示；新增组合占用弹窗（关组合输出保留接线）；comboLive 持久化恢复。
- **2026-11-01 下午（v52）**：占用弹窗确认后**自动按单路开启用户请求的通道**；关闭/开启后本地同步 STATUS 消除开关闪绿。
- **2026-11-01 傍晚（v53）**：「开始记录」按钮样式统一为实底；数据表默认 10 行（可拖拽）。
- **2026-11-01 傍晚（v54）**：**修复暗色主题约 27 个 CSS 变量循环自引用导致背景透明**（根因修复，补全真实色值）；预设弹层改实底浮层；**输出 ON 时改设定先自动关输出**。
- **2026-11-01 晚（v55）**：**记录间隔可配置**（0.8s~60s，后端 next_t 调度防追帧）；IT6302 原始能力调研（MEAS/FETCH 支持 ALL 一次回三通道，串口 0.2~0.3s/轮可行）；**git 初始化**（dev 分支）并推送 GitHub `lab-console`。
- **2026-11-02（v56）**：**IT8510 真机接入，发现是老款非"+"机型，重写驱动**。用户接上 IT8510（IT-E122/CH343 → COM19）后 SCPI 全波特率无响应；用 Blinkinlabs/itech_usb_serial 参考实现探测确认：26 字节二进制私有协议、默认 4800bps（S/N 3797370020, FW 01.90）。**量程同步修正**：原界面 120V/30A/300W 是错的（那是 IT8512 的规格），官方规格书 + 真机拒绝法实测为 **120V/20A/120W**（保护设定上限 130W，CR 最高 4000Ω，CC 分辨率 0.1mA；maxI=30A/maxP=300W 被仪器 ERR-param 拒绝）。改动：instruments.py 新增 `IT8510Legacy`（帧 AA+addr+cmd+payload+cksum，设置类 ACK 回 0x12+状态码 0x80/0xA0/0xB0，查询类回显命令码，必须先发 0x20=1 远程模式）；app.py 连接分支按 `LEGACY_SERIAL` 标志直接 (port,baud) 构造、自检走 `selftest_lines()` 专用流程；连接后 meta 记录实际 baud 并同步前端 `S.cfg` 回显（修复连接设置面板不回显实际端口/波特率）；`?v=56`。验证：通信自检 PASS（IDN/模式/设定/保护限/测量全查询成功）、CC 设定 1.5A 仪器回读确认、输入 ON/OFF 正常、轮询无错误。空载测量 V/I/P=0 为正确现象（输入端未接电源）。
- **2026-11-02（v57~v63）**：**波特率按型号限制 + 侧栏仪器条目改版**。用户要求把 8510 用不到的波特率档删掉，并排查其他型号同类坑。查证：三台仪器**都不支持 115200**（原下拉第一项，最易踩）；IT8510 老款仅 4800；IT8511A+ 4800/9600/19200/38400（IT8500+ 手册）；IT6302 4800/9600/38400（无 19200）。改动：instruments.py 注册表加 `baud_rates`/`baud_default`；app.py snapshot 暴露 `model_specs`；前端 `fillBaudSelect()` 按当前型号渲染下拉、型号切换自动带默认值、移除全部硬编码 9600。随后按用户要求侧栏改版：`devEntryHtml` 名称同行右侧加**型号徽章**（`.dev-model`），副行完整规格（电源"3通道 · 30V/3A"，负载"120V 20A 120W"），悬停 tooltip 详述（CH3 5V/3A、CC/CV/CR/CP）。CSS 迭代多轮：名称被 flex 压竖排（裸文本节点包 `.dev-name` span 禁收缩）、徽章被压成"IT…"（两条 .dev-model 规则冲突，flex-shrink:1 覆盖 0，合并为 0）、规格单行截断改 `-webkit-line-clamp:2` 折行。截图验收通过；`?v=63`。提交 0683d30。

- **2026-11-02 晚（v64）**：**修复组合模式下非成员通道误走组合分支的 bug**（用户反馈：CH1+CH2 串联输出时开 CH3 也弹"需先关闭 CH1/CH2"）。根因：change 委托里 `comboMeta(devId)` 按**视图**判断而非按**被点卡片**判断——组合视图里 CH3 是单路卡却被当组合卡，导致：①开 CH3 误弹接线指引；②**改 CH3 设定会被错误分解写到 CH1/CH2**（更严重的隐藏 bug）；③关 CH3 会误清 comboLive。修复：`m = card.classList.contains('combo') ? comboMeta(devId) : null`（按卡片 .combo class 判断）。验证：探针拦截 api（放行 /api/status 保页面状态机）——组合接通后开 CH3 无弹窗直达 output{ch:3}、改 CH3 设定仅下发 set{ch:3}、CH3 开关不清 comboLive、组合卡自身开启仍正常弹接线指引。`?v=64`。

- **2026-11-02 深夜（v65/v66/v67）**：**回读精度 + 电流轴修复**（用户连续反馈：表格功率全 0 与曲线 0.003 对不上→精度不损失→CH2 仍 3 位小数→电流曲线无纵轴）。①后端 poll 舍入放宽：v/i/p 由 round(4/4/3) 改 round(6/6/6)，不再丢仪器有效位；②前端 fmtI/fmtP：小值保留 3 位有效数字去尾零（0.0002A/0.00288W），卡片读数、数据表、总功率统一；③**电流 series 丢失 axis:'right'（v55 基线就有的老 bug）**——电流画在 0~12V 电压轴上贴底恒为 0、青色电流轴从未创建；电源/负载两视图补上 axis:'right'；④轴刻度 fmt 支持小步长（按量级给位去尾零）；⑤plot 双右轴 padR 68→108（电流刻度外移 pw+54，修标签被画布裁剪）。验证：页面内 getImageData 扫描三张 canvas 标签区均有青色刻度像素；截图人工复核三列刻度（V 蓝/W 橙/A 青）齐全。`?v=67`。注意：hidden 窗口 getBoundingClientRect 不可靠（三 canvas 重叠 125px 高），量测必须用可见窗口。

- **2026-11-02 深夜（v68）**：数据表对齐 + 游标精度 + 右轴对齐格线（用户 5 点反馈）。①数据表 U/I/P 统一 toFixed(6) 补零、左对齐（用户要求按位数在后边补 0）；CSV 确认保留 round6 全精度，时间列加毫秒（_csv_time，0.8s 间隔不再同秒重复）；②游标读数框按量纲走 fmtV/fmtI/fmtP 高精度（不再统一 3~4 位丢位）；③右轴刻度改为与左轴格线 y 严格对齐：每条格线上方标功率值（橙）、下方标电流值（青），值由各自轴量程反算，数字、格线、曲线三者可对读；④数据表历史 40 行扩到 200 行（原来约 32s 就滚没了）；⑤验证：DOM 读单元格文本（P 列 0.000001 共 6 位）+ 可见窗口截图（橙/青交错紧贴格线）。?v=68。

- **2026-11-03 凌晨（v69）**：轴布局修正 + 曲线时间缩放（用户 3 点反馈）。①轴单位标签（V/A/W）移到绘图区上缘之外（padT 2→16，单位画在 padT-4），不再与最上面的刻度值（原与 W 重合的顶部功率值）重叠；②右侧功率（橙）/电流（青）刻度合并为**同一列左对齐**（格线上方功率、下方电流），padR 108→64；单位合并显示为 A/W；③**曲线数据改为全量历史均匀降采样**：后端 snapshot 对 history（20000 点≈4.4h）ceil 步长取点≤360、末点保持实时 → 横轴时间跨度随记录时长自动扩展，老数据不再被截断丢弃（原来只回最近 360 点≈4.8min，之后旧曲线点被刷掉）；数据表另走新增的 recent 字段（最近 200 个原始点），不受降采样影响；④修数据表 P 列被裁：单元格 padding 10→5px（表 305px>容器 287px 裁掉尾 2 位，实际文本一直是 6 位补零）。验证：可见窗口截图（A/W 独立行、单列橙上青下、表格 6 位完整）+ DOM 读宽（table 275≤287）。?v=69。

- **2026-11-03（v70）**：右轴标签重复与间隔不均修复（用户问"为啥两个 0.004"）。根因：右轴标签按格线高度反算，值不是轴刻度步长的整数倍，而小数位按步长取——相邻格线的实际间隔比步长细时两个不同值舍入成同一标签。修复：①新 fmtPrec(v, step)：位数=ceil(-log10(相邻格线实际数值间隔))+1（上限 7），相邻标签至少差 1 位、永不重字；②标签固定位数不去尾零（与表格补零偏好一致），列内对齐。实测功率 0.0050/0.0033/0.0017/0.0000、电流 0.00040/0.00027/0.00013/0.00000，间隔均匀。?v=70。

- **2026-11-03（v70）**：右轴标签重复与间隔不均修复（用户问"为啥两个 0.004"）。根因：右轴标签按格线高度反算，值不是轴刻度步长的整数倍，而小数位按步长取——相邻格线的实际间隔比步长细时两个不同值舍入成同一标签。修复：①新 fmtPrec(v, step)：位数=ceil(-log10(相邻格线实际数值间隔))+1（上限 7），相邻标签至少差 1 位、永不重字；②标签固定位数不去尾零（与表格补零偏好一致），列内对齐。实测功率 0.0050/0.0033/0.0017/0.0000、电流 0.00040/0.00027/0.00013/0.00000，间隔均匀。?v=70。

- **2026-11-03（v71）**：右轴刻度改为整数间隔（用户拍板：格线对齐方案产生 0.00027 这类零散尾数，不如取整好看——"根据就近间隔取整数作为纵轴，比如 0.001 0.002"）。回到各自轴独立 nice 刻度（fitTicks 的 1/2/2.5/5x10n 步长）画在真实高度，同列左对齐（功率橙/电流青），两组标签相距 <11px 时功率让位防重叠。实测电流 0.00000~0.00040 步进 0.0001、功率 0.002/0.003。fmtPrec 保留备用。?v=71。

- **2026-11-03（v71）**：右轴刻度改为整数间隔（用户拍板：格线对齐方案产生 0.00027 这类零散尾数，不如取整好看——"根据就近间隔取整数作为纵轴，比如 0.001 0.002"）。回到各自轴独立 nice 刻度（fitTicks 的 1/2/2.5/5x10n 步长）画在真实高度，同列左对齐（功率橙/电流青），两组标签相距 <11px 时功率让位防重叠。实测电流 0.00000~0.00040 步进 0.0001、功率 0.002/0.003。fmtPrec 保留备用。?v=71。

- **2026-11-03（v72）**：修 v71 引入的两个问题。①右轴标签悬空无数值锚点→右缘画 4px 同色短刻度线（电流青/功率橙各在自己 y）；②CH2/CH3 功率标签全部消失→全 0 时电流/功率轴都回退同一备选量程，两组标签 y 完全重合，让位逻辑把功率全跳过——改为重叠时上下错开（功率 y-9/电流 y+9）而非单侧让位。实测三通道功率轴均显示。?v=72。

- **2026-11-03（v72）**：修 v71 引入的两个问题。①右轴标签悬空无数值锚点→右缘画 4px 同色短刻度线（电流青/功率橙各在自己 y）；②CH2/CH3 功率标签全部消失→全 0 时电流/功率轴都回退同一备选量程，两组标签 y 完全重合，让位逻辑把功率全跳过——改为重叠时上下错开（功率 y-9/电流 y+9）而非单侧让位。实测三通道功率轴均显示。?v=72。

- **2026-11-03（v73）**：实时读数位数统一（用户问 CH2 精度为何与 CH1/CH3 不同——CH2 是真 0，旧 fmtI/fmtP 对 0 只给 3 位、非零小值给 4~5 位）。fmtV/fmtI/fmtP 改为统一 toFixed(6) 补零：卡片大读数、游标读数框、总输出功率、负载读数全部与数据表/CSV 同为 6 位，任何通道任何位置位数恒一致。?v=73。

- **2026-11-03（v73）**：实时读数位数统一（用户问 CH2 精度为何与 CH1/CH3 不同——CH2 是真 0，旧 fmtI/fmtP 对 0 只给 3 位、非零小值给 4~5 位）。fmtV/fmtI/fmtP 改为统一 toFixed(6) 补零：卡片大读数、游标读数框、总输出功率、负载读数全部与数据表/CSV 同为 6 位，任何通道任何位置位数恒一致。?v=73。

- **2026-11-03（v74）**：统一格线系统（用户要求右轴数值按格线对齐、格线怼到整齐小数值上）。三轴共用 N 段格线（N=spanY/30，2~6），各轴量程=N×整数步长（1/2/2.5/5x10n，niceStep），电压/电流/功率刻度全部是整齐值且精确落在同一条格线上；同格线功率标签在上(-9)/电流在下(+9)，右缘同色短刻度线锚点保留。左轴电压也随之变整齐（如 0/5/10...）。实测 CH1 0~0.005W/0.00000~0.00050A、CH2 0.0~0.5W/0.0~1.0A、CH3 全部对齐。?v=74。

- **2026-11-03（v74）**：统一格线系统（用户要求右轴数值按格线对齐、格线怼到整齐小数值上）。三轴共用 N 段格线（N=spanY/30，2~6），各轴量程=N×整数步长（1/2/2.5/5x10n，niceStep），电压/电流/功率刻度全部是整齐值且精确落在同一条格线上；同格线功率标签在上(-9)/电流在下(+9)，右缘同色短刻度线锚点保留。左轴电压也随之变整齐（如 0/5/10...）。实测 CH1 0~0.005W/0.00000~0.00050A、CH2 0.0~0.5W/0.0~1.0A、CH3 全部对齐。?v=74。

- **2026-11-03（v75）**：轴刻度统一不补零（用户指出功率 0.005 没补 0 但电流 0.00050 补了）。fmt 小步长分支恢复去尾零（保留至少 3 位）：电流 0.0005/0.0004/...，与功率风格一致；仍与格线对齐。卡片/表格/CSV 的 6 位补零不变（fmtV/fmtI/fmtP 独立）。?v=75。

- **2026-11-03（v75）**：轴刻度统一不补零（用户指出功率 0.005 没补 0 但电流 0.00050 补了）。fmt 小步长分支恢复去尾零（保留至少 3 位）：电流 0.0005/0.0004/...，与功率风格一致；仍与格线对齐。卡片/表格/CSV 的 6 位补零不变（fmtV/fmtI/fmtP 独立）。?v=75。

- **2026-11-03（v76）**：①右缘短刻度线跟随各自标签行错开（功率橙杠在上/电流青杠在下）——统一格线系统下两组刻度都落在格线 y 上，原来两条杠完全重叠无法分辨颜色归属；②浏览器模式补上存储目录选择：后端新增 /api/fs/list（列盘符/子目录，模块级 list_dirs）与 /api/fs/mkdir（新建文件夹），前端新增 #dir-mask 目录浏览弹窗（路径栏+上一级+目录列表+新建文件夹+选择此目录），记录对话框的「选择目录」按钮始终显示：桌面版走 pywebview 系统对话框，浏览器模式走自制选择器；「选择目录」按钮也从文件名行挪到存储目录行。DOM 探针全流程验证（盘符→进目录→新建→回填 rec-dir）。?v=76。

- **2026-11-03（v76）**：①右缘短刻度线跟随各自标签行错开（功率橙杠在上/电流青杠在下）——统一格线系统下两组刻度都落在格线 y 上，原来两条杠完全重叠无法分辨颜色归属；②浏览器模式补上存储目录选择：后端新增 /api/fs/list（列盘符/子目录，模块级 list_dirs）与 /api/fs/mkdir（新建文件夹），前端新增 #dir-mask 目录浏览弹窗（路径栏+上一级+目录列表+新建文件夹+选择此目录），记录对话框的「选择目录」按钮始终显示：桌面版走 pywebview 系统对话框，浏览器模式走自制选择器；「选择目录」按钮也从文件名行挪到存储目录行。DOM 探针全流程验证（盘符→进目录→新建→回填 rec-dir）。?v=76。

- **2026-11-03（v77）**：右缘指示杠改回格线正位（用户反馈双杠分行后位置脱离虚线）。每条格线右端一根中性灰白短杠（非橙非青），正对格线 y；功率(上)/电流(下)数值围绕它对读。?v=77。

- **2026-11-03（v77）**：右缘指示杠改回格线正位（用户反馈双杠分行后位置脱离虚线）。每条格线右端一根中性灰白短杠（非橙非青），正对格线 y；功率(上)/电流(下)数值围绕它对读。?v=77。

- **2026-11-03（v78）**：修目录选择弹窗两个 UI 问题（用户反馈）。①弹窗被「开始记录」盖住——dir-mask 在 DOM 中排在 rec-mask 之前、同为 z-index:200，后者覆盖前者 → #dir-mask z-index 提到 220；②「选择目录」按钮文字溢出——tiny 按钮在 .row flex 里被压缩 → button.tiny 加 flex:0 0 auto + white-space:nowrap。另向用户说明：浏览器模式因安全模型无法调用系统原生目录对话框（拿不到本地完整路径），桌面版走 pywebview 原生对话框，浏览器模式只能用自制目录浏览弹窗。?v=78。

- **2026-11-03（v78）**：修目录选择弹窗两个 UI 问题（用户反馈）。①弹窗被「开始记录」盖住——dir-mask 在 DOM 中排在 rec-mask 之前、同为 z-index:200，后者覆盖前者 → #dir-mask z-index 提到 220；②「选择目录」按钮文字溢出——tiny 按钮在 .row flex 里被压缩 → button.tiny 加 flex:0 0 auto + white-space:nowrap。另向用户说明：浏览器模式因安全模型无法调用系统原生目录对话框（拿不到本地完整路径），桌面版走 pywebview 原生对话框，浏览器模式只能用自制目录浏览弹窗。?v=78。

- **2026-11-03（v78 打包）**：重新打包 dist/ITECH测试台.exe（PyInstaller onefile，13.2MB，内嵌 web v78 界面），含 v56~v78 全部更新（IT8510 老协议、真机精度链路、统一格线系统、目录选择器、按钮/弹窗修复）。启动冒烟通过（进程存活、窗口正常）。exe 不入库（.gitignore 排除 dist/）。

- **2026-11-03（v0.0.1 发布）**：首个 GitHub Release。新增 Inno Setup 脚本 setup.iss（AppVersion 0.0.1，安装 LabConsole.exe 到 Program Files，开始菜单/桌面快捷方式/卸载，随装 README/DEVLOG），编译 Output/LabConsole-Setup-v0.0.1.exe（15MB）。Release: https://github.com/li-wanxin/lab-console/releases/tag/v0.0.1 附件 LabConsole-Setup-v0.0.1.exe（安装包）+ LabConsole-Portable-v0.0.1.exe（便携=原 ITECH测试台.exe）。工具链：winget 装 Inno Setup 6（本机 %LOCALAPPDATA%\Programs）与 gh CLI；gh 走系统代理 127.0.0.1:7890（GitHub HTTPS 直连被墙，SSH 22 直连可用）；gh auth login --web 设备码授权登录 li-wanxin。中文文件名附件 gh 上传会改名，用英文副本名上传。

- **2026-11-03（v0.0.1 发布）**：首个 GitHub Release。新增 Inno Setup 脚本 setup.iss（AppVersion 0.0.1，安装 LabConsole.exe 到 Program Files，开始菜单/桌面快捷方式/卸载，随装 README/DEVLOG），编译 Output/LabConsole-Setup-v0.0.1.exe（15MB）。Release: https://github.com/li-wanxin/lab-console/releases/tag/v0.0.1 附件 LabConsole-Setup-v0.0.1.exe（安装包）+ LabConsole-Portable-v0.0.1.exe（便携=原 ITECH测试台.exe）。工具链：winget 装 Inno Setup 6（本机 %LOCALAPPDATA%\Programs）与 gh CLI；gh 走系统代理 127.0.0.1:7890（GitHub HTTPS 直连被墙，SSH 22 直连可用）；gh auth login --web 设备码授权登录 li-wanxin。中文文件名附件 gh 上传会改名，用英文副本名上传。

- **2026-11-03 晚（v79）**：**IT8511A+ 真机接入**（用户配了兼容 IT-E122 模块）。重要认知：IT8511A+ 后面板 DB9 是 **TTL 电平**口（官方 CAUTION：直插标准 RS-232 线可能损坏仪器），必须经 IT-E121/E122 通信模块连电脑；协议与 IT8510 老款同族（IT8500 26 字节帧），但**默认 9600bps**（IT8510 是 4800——这就是之前按型号连不上的根因，地址遍历 0~31 x 波特率扫描定位）。程序改动：新增 IT8511APlusLegacy(IT8510Legacy 子类，model/量程 150V/30A/150W 覆盖)，LOAD_DRIVERS[IT8511A+] 从 SCPI 类改指向 Legacy 子类（TTL 口不讲 SCPI）。真机验证：9600 连接成功，IDN=ITECH,IT8511A,SN:4747410185,FW:01.46。另：gh CLI 已设备码授权（走代理 127.0.0.1:7890），后续 Release 直接可用。

- **2026-11-03 晚（v79）**：**IT8511A+ 真机接入**（用户配了兼容 IT-E122 模块）。重要认知：IT8511A+ 后面板 DB9 是 **TTL 电平**口（官方 CAUTION：直插标准 RS-232 线可能损坏仪器），必须经 IT-E121/E122 通信模块连电脑；协议与 IT8510 老款同族（IT8500 26 字节帧），但**默认 9600bps**（IT8510 是 4800——这就是之前按型号连不上的根因，地址遍历 0~31 x 波特率扫描定位）。程序改动：新增 IT8511APlusLegacy(IT8510Legacy 子类，model/量程 150V/30A/150W 覆盖)，LOAD_DRIVERS[IT8511A+] 从 SCPI 类改指向 Legacy 子类（TTL 口不讲 SCPI）。真机验证：9600 连接成功，IDN=ITECH,IT8511A,SN:4747410185,FW:01.46。另：gh CLI 已设备码授权（走代理 127.0.0.1:7890），后续 Release 直接可用。


- **2026-11-03 深夜：IT8511A+ SCPI 实机验证（应雪验证，结论：不支持）**。用户提供《IT8500plus Programming Guide-CN - SCPI.pdf》要求再试 SCPI。实测（COM8=CH343 IT-E122，负载 TTL 单线半双工，RX 有 TX 回环，响应偶发 ~2/6）：\*IDN? 在 9600/4800/19200/38400 x {\n,\r,\r\n} x 多轮重试全部无响应；同链路帧协议 0x20 0x5F 读版本帧有响应（AA 00 12 90 00 01 5F 2A 0D，响应 cmd=0x12）。SYST:REM 前置也无效。手册无协议切换菜单说明；EEVblog 同机型用户证实 TTL 口只有私有帧协议。结论：该机（FW 01.46）串口仅支持 IT8500 帧协议，SCPI 手册适用于带原生 USB/RS232 的新机型（IT8513B+/IT8514+/IT8516C+ 等）或需升级固件。v79 Legacy 驱动路线正确，不改代码。探测脚本存于工作区根 scpi_probe*.py。
- **2026-11-04 凌晨（v80）**：四项调整。① **CP 改名 CW**：恒功率档全链路（前端按钮/MODE_META/maxFor 映射、后端 load_set 校验、_check_load_range、IT8510Legacy._MODE_CODE/_LEVEL_*、SimLoad、死代码 IT8511A SCPI 类）统一叫 CW（IT8500+ 面板官方叫法 Constant Wattage），帧协议命令码不变（CW=0x02、电平 0x2E/0x2F）。② **负载改设定先关输入**：load_set 在 state.on 为真时先 set_input(False) 并保持 OFF（与电源 v54 行为对齐），日志+toast 提示手动重开；模式切换与数值修改两条前端路径都覆盖。③ **IT8511A+ 波特率只留 9600**：LOAD_MODEL_SPECS.baud_rates=[9600]（真机实测其他速率无响应），前端 fillBaudSelect 自动跟随。④ **全精度显示**：后端删除所有测量/设定值 round（poll_once、_read_power_setpoints、power_set、load_set、sweep 两处），前端 fmtV/fmtI/fmtP 统一走 fmtFull（10 位有效数字去尾零），卡片实测值/状态标签/曲线游标/数据表/组合摘要全部生效；轴刻度 niceStep 系统不变。index.html v=80。模拟模式冒烟：CW 设定、ON 时改 level 自动 OFF、CP 拒绝，全部通过；截图 tools/shot_v80_load.png 验收。

- **2026-11-04 凌晨（v81）**：应用户反馈回退 v80 第④项的前端部分——显示格式恢复为**固定 6 位小数补零**（fmtV/fmtI/fmtP 恢复 toFixed(6)，删 fmtFull；卡片实测 3 位、数据表 6 位、摘要行 2/3 位，全同 v79 样式）。保留：CW 命名、改设定自动关输入、IT8511A+ 波特率仅 9600、**后端数据不 round（全精度落库/CSV）**——只动显示层。index.html v=81。已提交 338c48c 推送。教训：用户对"显示 0 而非 0.000"很敏感，全精度≠去补零，显示格式变更必须先问。

- **2026-11-04 凌晨（v0.0.2 发布）**：① 顶部标题旁新增**程序版本徽章**——app.py 新增 APP_VERSION="0.0.2" 常量并随 /api/status 下发 app_version 字段，前端 render() 渲染 #app-ver（样式 .app-ver 与徽章一致）。② setup.iss 升 0.0.2（MyAppVersion/VersionInfo*），PyInstaller 重打包 dist/ITECH测试台.exe（13.2MB，内嵌 v82 界面），ISCC 编译 Output/LabConsole-Setup-v0.0.2.exe（15MB）。③ GitHub Release v0.0.2：附件 LabConsole-Setup-v0.0.2.exe + LabConsole-Portable-v0.0.2.exe（便携版英文副本，中文文件名上传会被改名）。界面资源 v82。发布后注意：APP_VERSION 以后每次发版要同步改（与 setup.iss 一致）。

- **2026-11-05 上午（界面 v83，记录导出改版）**：**记录导出从 CSV 改为 Excel (.xlsm) + 导出选项对话框**。流程：结束记录 → 停止采样并挂起（recordings→pending_exports，快照新增 pending 列表）→ 前端弹 #export-mask 对话框勾选「测试概览」「U/I/W 曲线图」（默认都勾）→ POST /api/record/export{id,overview,chart} 生成 xlsm；取消可关窗，rec-bar 留"待导出"条目（导出 Excel/丢弃按钮，/api/record/discard）。xlsm 结构：[概览(可选,第一页)] + 数据 + [曲线图(可选,最后页)]；概览含仪器名/型号/IDN、记录对象、起止时间、点数、间隔、各数值列 min/max/avg；曲线为 openpyxl LineChart（数据源引用数据表，时间类别轴）。数据表时间列为 datetime（格式 yyyy-mm-dd hh:mm:ss.000），record_start 文件名强制 .xlsm，重名检查涵盖 pending。依赖 openpyxl>=3.1（requirements + PyInstaller --collect-all openpyxl，dist exe 已重打包 20.2MB）。验证：模拟端到端（负载单路/电源双输出）、无概览分支、读回 sheet 顺序与统计、对话框截图。IV 扫描导出仍为 CSV（未在本次范围）。APP_VERSION 仍为 0.0.2（未发版；下次发版记得同步）。

- **2026-11-05 上午（界面 v83，记录导出改版）**：**记录导出从 CSV 改为 Excel (.xlsm) + 导出选项对话框**。流程：结束记录 → 停止采样并挂起（recordings→pending_exports，快照新增 pending 列表）→ 前端弹 #export-mask 对话框勾选「测试概览」「U/I/W 曲线图」（默认都勾）→ POST /api/record/export{id,overview,chart} 生成 xlsm；取消可关窗，rec-bar 留"待导出"条目（导出 Excel/丢弃按钮，/api/record/discard）。xlsm 结构：[概览(可选,第一页)] + 数据 + [曲线图(可选,最后页)]；概览含仪器名/型号/IDN、记录对象、起止时间、点数、间隔、各数值列 min/max/avg；曲线为 openpyxl LineChart（数据源引用数据表，时间类别轴）。数据表时间列为 datetime（格式 yyyy-mm-dd hh:mm:ss.000），record_start 文件名强制 .xlsm，重名检查涵盖 pending。依赖 openpyxl>=3.1（requirements + PyInstaller --collect-all openpyxl，dist exe 已重打包 20.2MB）。验证：模拟端到端（负载单路/电源双输出）、无概览分支、读回 sheet 顺序与统计、对话框截图。IV 扫描导出仍为 CSV（未在本次范围）。APP_VERSION 仍为 0.0.2（未发版；下次发版记得同步）。

- **2026-11-05 上午（导出报错处理）**：用户真机导出报 "Error 13 Permission denied"。复现确认根因：**目标 .xlsm 文件被 Excel/WPS 独占占用**（Windows 打开 Excel 即锁文件），wb.save 写不进。数据安全设计验证：record_export 中 pending pop 在成功之后，失败不丢数据，可重试。改进：PermissionError 单独捕获，错误提示改为中文指引（"关闭 Excel 后在记录条上点「导出 Excel」重试；数据未丢失"）。dist 便携版重打包（运行中无法覆盖 → 打包到 dist_new 后成功替换，08:36 版 20.2MB）。复现脚本 repro_err13.py（工作区根，FileShare.None 独占锁模拟 Excel；注意 python os.open 句柄不锁文件，不能用来模拟）。

- **2026-11-05 上午（导出报错处理）**：用户真机导出报 "Error 13 Permission denied"。复现确认根因：**目标 .xlsm 文件被 Excel/WPS 独占占用**（Windows 打开 Excel 即锁文件），wb.save 写不进。数据安全设计验证：record_export 中 pending pop 在成功之后，失败不丢数据，可重试。改进：PermissionError 单独捕获，错误提示改为中文指引（"关闭 Excel 后在记录条上点「导出 Excel」重试；数据未丢失"）。dist 便携版重打包（运行中无法覆盖 → 打包到 dist_new 后成功替换，08:36 版 20.2MB）。复现脚本 repro_err13.py（工作区根，FileShare.None 独占锁模拟 Excel；注意 python os.open 句柄不锁文件，不能用来模拟）。

- **2026-11-05 上午（导出格式修复：xlsm→xlsx）**：用户真机导出的 .xlsm 用 Excel 打不开（"文件格式或扩展名无效"）。排查：文件 zip 结构完整、openpyxl 可读、sheet 齐全——坏在 **Excel 对 .xlsm 硬性要求内部携带 vbaProject.bin（VBA 宏项目）**，openpyxl 从零创建的工作簿没有宏项目，产出的 .xlsm 不合法（上次只验证了 zip 结构，教训）。修复：导出格式全面改 **.xlsx**（无宏要求，Excel 100% 兼容；三页结构/概览统计/图表全部不变），record_start 强制后缀 .xlsx（填 .xlsm 也自动转），前端默认名与文案同步。已验证 content type 为标准 xlsx main+xml、三页齐全。用户桌面已有的坏文件：直接把扩展名改成 .xlsx 即可打开（内容是好的）。dist exe 重打包（08:46 版 20.2MB）。若用户将来确需带宏的 .xlsm：方案是内置一个带空宏项目的 xlsm 模板 + load_workbook(keep_vba=True)，待用户确认再做。

- **2026-11-05 上午（导出格式修复：xlsm→xlsx）**：用户真机导出的 .xlsm 用 Excel 打不开（"文件格式或扩展名无效"）。排查：文件 zip 结构完整、openpyxl 可读、sheet 齐全——坏在 **Excel 对 .xlsm 硬性要求内部携带 vbaProject.bin（VBA 宏项目）**，openpyxl 从零创建的工作簿没有宏项目，产出的 .xlsm 不合法（上次只验证了 zip 结构，教训）。修复：导出格式全面改 **.xlsx**（无宏要求，Excel 100% 兼容；三页结构/概览统计/图表全部不变），record_start 强制后缀 .xlsx（填 .xlsm 也自动转），前端默认名与文案同步。已验证 content type 为标准 xlsx main+xml、三页齐全。用户桌面已有的坏文件：直接把扩展名改成 .xlsx 即可打开（内容是好的）。dist exe 重打包（08:46 版 20.2MB）。若用户将来确需带宏的 .xlsm：方案是内置一个带空宏项目的 xlsm 模板 + load_workbook(keep_vba=True)，待用户确认再做。

- **2026-11-05 上午（时间列宽修复）**：用户反馈导出 Excel 时间列太窄显示 ####（"yyyy-mm-dd hh:mm:ss.000" 共 23 字符，原宽 21 不够）。数据表 A 列宽 21→24，验证通过。dist exe 重打包（08:51 版）。

- **2026-11-05 上午（时间列宽修复）**：用户反馈导出 Excel 时间列太窄显示 ####（"yyyy-mm-dd hh:mm:ss.000" 共 23 字符，原宽 21 不够）。数据表 A 列宽 21→24，验证通过。dist exe 重打包（08:51 版）。

- **2026-11-05 上午（v0.0.3 发布：自动更新功能）**：**启动联网检查更新**。后端：Bench 启动 3s 后 daemon 线程查 GitHub API releases/latest（UA 必带，timeout 8s，失败静默），tag 三段数字比较判断 available，结果随 /api/status 的 update 字段下发（含 assets 列表）。POST /api/update/download：按 exe 名判断形态——LabConsole*（Inno 安装版）→ 下载 Setup 到 %TEMP% 并 os.startfile 启动安装向导覆盖安装；ITECH测试台.exe（便携版）→ 下载 Portable 到 %TEMP%，利用 Windows 允许改名运行中 exe 的特性三步就位替换（新包→stem_更新下载.exe，当前 exe→stem_旧版本.exe，新包改名回原名），失败回滚提示手动替换；开发模式只打开发布页。前端 #update-mask 弹窗（非强制：立即更新 / 查看看板 / 忽略此版本，忽略版本号存 localStorage，render 里 maybeShowUpdateDialog 防重弹）。真实验证：8799 服务正确查到 latest v0.0.2 与两个 asset，本地 0.0.3 > 0.0.2 判断不弹。APP_VERSION=0.0.3，setup.iss 同步，界面 v84，dist 便携版 20.2MB + Output 安装包 22MB 均已产出，发 Release v0.0.3。

- **2026-11-05 上午（v0.0.3 发布：自动更新功能）**：**启动联网检查更新**。后端：Bench 启动 3s 后 daemon 线程查 GitHub API releases/latest（UA 必带，timeout 8s，失败静默），tag 三段数字比较判断 available，结果随 /api/status 的 update 字段下发（含 assets 列表）。POST /api/update/download：按 exe 名判断形态——LabConsole*（Inno 安装版）→ 下载 Setup 到 %TEMP% 并 os.startfile 启动安装向导覆盖安装；ITECH测试台.exe（便携版）→ 下载 Portable 到 %TEMP%，利用 Windows 允许改名运行中 exe 的特性三步就位替换（新包→stem_更新下载.exe，当前 exe→stem_旧版本.exe，新包改名回原名），失败回滚提示手动替换；开发模式只打开发布页。前端 #update-mask 弹窗（非强制：立即更新 / 查看看板 / 忽略此版本，忽略版本号存 localStorage，render 里 maybeShowUpdateDialog 防重弹）。真实验证：8799 服务正确查到 latest v0.0.2 与两个 asset，本地 0.0.3 > 0.0.2 判断不弹。APP_VERSION=0.0.3，setup.iss 同步，界面 v84，dist 便携版 20.2MB + Output 安装包 22MB 均已产出，发 Release v0.0.3。

- **2026-11-07 上午（曲线图美化，应用户真机反馈）**：用户真机导出文件（负载 377 点）三大问题逐一排查修复：① **没曲线**——旧实现把 6 列全部丢进一个折线图，"工作模式"文本列混入导致 Excel 渲染异常；② **轴刻度/单位缺失**——openpyxl 默认 x_axis.axPos='l'（左侧！应为底部）、缺 tickLblPos 与 delete=False 显式设置，且类别轴 numRef 引用 datetime 列显示内部序列号而非时间；③ **只画 U/I/W**。新实现：「曲线图」页改为**三张独立图**（电压/V、电流/A、功率/W 各自 Y 轴标题带单位、x 轴时间、axPos/tickLblPos/delete 全部显式修正、单系列无图例），数据表尾部加灰色辅助列"时间(曲线用)"（字符串 hh:mm:ss）作类别源（datetime numRef 坑），电源多路时每图多系列（逐系列单独 add_data 兼容不连续列）。用用户真实文件（377 点）重导验证：3 图结构、引用列仅 B/C/D+G、轴属性全部正确，产出 桌面/负载1记录_20261107_085425_美化验证.xlsx 供用户 Excel 实机确认。已提交推送（界面未变版仍 v84）。

- **2026-11-07 上午（曲线图美化，应用户真机反馈）**：用户真机导出文件（负载 377 点）三大问题逐一排查修复：① **没曲线**——旧实现把 6 列全部丢进一个折线图，"工作模式"文本列混入导致 Excel 渲染异常；② **轴刻度/单位缺失**——openpyxl 默认 x_axis.axPos='l'（左侧！应为底部）、缺 tickLblPos 与 delete=False 显式设置，且类别轴 numRef 引用 datetime 列显示内部序列号而非时间；③ **只画 U/I/W**。新实现：「曲线图」页改为**三张独立图**（电压/V、电流/A、功率/W 各自 Y 轴标题带单位、x 轴时间、axPos/tickLblPos/delete 全部显式修正、单系列无图例），数据表尾部加灰色辅助列"时间(曲线用)"（字符串 hh:mm:ss）作类别源（datetime numRef 坑），电源多路时每图多系列（逐系列单独 add_data 兼容不连续列）。用用户真实文件（377 点）重导验证：3 图结构、引用列仅 B/C/D+G、轴属性全部正确，产出 桌面/负载1记录_20261107_085425_美化验证.xlsx 供用户 Excel 实机确认。已提交推送（界面未变版仍 v84）。

- **2026-11-07 上午（曲线图合并单图）**：应用户反馈"三张图颜色繁多、要合并成一张"。IT8511A+ 三量纲数值范围同为 0~150（V/A/W），共轴可读——「曲线图」页改回**单张图**：三条曲线固定三色（电压蓝 2E75B6 / 电流红 C00000 / 功率绿 548235，电源多路时扩展调色板循环），Y 轴标题"数值 (V / A / W)"，图例底部，单系列无图例，线宽 1.4pt。轴属性修正保持（axPos/tickLblPos/delete/时间文本辅助列）。用用户真实数据（377 点）重导验证：1 图 3 系列固定色、引用列 B/C/D+G，产出桌面 ..._单图三色.xlsx。dist exe 重打包（09:4x 版，含本轮全部图表修复）。

- **2026-11-07 上午（曲线图合并单图）**：应用户反馈"三张图颜色繁多、要合并成一张"。IT8511A+ 三量纲数值范围同为 0~150（V/A/W），共轴可读——「曲线图」页改回**单张图**：三条曲线固定三色（电压蓝 2E75B6 / 电流红 C00000 / 功率绿 548235，电源多路时扩展调色板循环），Y 轴标题"数值 (V / A / W)"，图例底部，单系列无图例，线宽 1.4pt。轴属性修正保持（axPos/tickLblPos/delete/时间文本辅助列）。用用户真实数据（377 点）重导验证：1 图 3 系列固定色、引用列 B/C/D+G，产出桌面 ..._单图三色.xlsx。dist exe 重打包（09:4x 版，含本轮全部图表修复）。

- **2026-11-07 上午（双 Y 轴曲线）**：应用户要求改左右双数轴。「曲线图」单图改为**双轴组**：主 lineChart 组（电压蓝 2E75B6 + 功率绿 548235）→ 左轴"电压 / V · 功率 / W"（crosses=autoZero，带网格线）；次 lineChart 组（电流红 C00000）→ 右轴"电流 / A"（axId=200、crosses=max、majorGridlines=None 防两套网格交叉）。两 lineChart 组共享 catAx（axId=10）。openpyxl 用 `主图 += 次图` 合并，次图只需 y_axis.axId=200 + crosses=max。XML 核验：2 组/2 valAx/1 catAx/axId 10+100+200/三色固定/引用列 B/C/D+G，全部正确。产出桌面 ..._双轴.xlsx 供用户确认。dist exe 重打包（09:5x 版）。

- **2026-11-07 上午（双 Y 轴曲线）**：应用户要求改左右双数轴。「曲线图」单图改为**双轴组**：主 lineChart 组（电压蓝 2E75B6 + 功率绿 548235）→ 左轴"电压 / V · 功率 / W"（crosses=autoZero，带网格线）；次 lineChart 组（电流红 C00000）→ 右轴"电流 / A"（axId=200、crosses=max、majorGridlines=None 防两套网格交叉）。两 lineChart 组共享 catAx（axId=10）。openpyxl 用 `主图 += 次图` 合并，次图只需 y_axis.axId=200 + crosses=max。XML 核验：2 组/2 valAx/1 catAx/axId 10+100+200/三色固定/引用列 B/C/D+G，全部正确。产出桌面 ..._双轴.xlsx 供用户确认。dist exe 重打包（09:5x 版）。

- **2026-11-07 上午（轴标题重叠美化）**：用户截图反馈轴标题与轴数字/刻度重叠。原因：左轴长中文标题"电压 / V · 功率 / W"竖排超高挤压绘图区，x 轴标题"时间"与底部图例/时间刻度三方挤压。修复：轴标题只留短单位（左"V / W"、右"A"），删除 x 轴标题"时间"（时间刻度自明）；完整量纲中文名在图例系列名（电压_V/功率_W/电流_A）里保留。XML 核验图表文字仅剩 [标题, V/W, A]。dist exe 重打包（10:0x 版）。

## 六、当前状态（v80）

- dev 分支已推送远程（最新 0683d30）。本地目录 `D:/easyCode/lab-console`（原 itech-bench，仓库直接克隆到新目录）。
- 另有一份工作副本 `C:/Users/liwanxin/WorkBuddy/2026-10-28-22-40-24/itech-bench`（WorkBuddy 工作区，已同步 v64 修复；D:/easyCode 不在本机，两处改动需经 GitHub 同步）。
- 设备清单：电源1（IT6302，未连接）+ 负载1（IT8510 型号）。服务重启会重置清单，真机重连即可（2026-11-02 深夜曾代连 COM8@9600 并验证回读精度，连接后先关断生效）。服务重启会重置清单，真机重连即可（2026-11-02 深夜曾代连 COM8@9600 并验证回读精度，连接后先关断生效）。**IT8510 真机已验证**（COM19@4800，自检 PASS、CC 设定回读、输入开关均正常）；服务重启后设备清单重置，重连即可（波特率下拉现在只给 4800，选不错）。
- 波特率下拉已按型号限制（v57~v63）：IT8510 仅 4800；IT8511A+/IT6302 无 115200。
- IT6302 电源侧真机仍未验证；`dist/` exe 是旧界面，未重新打包。

## 七、待办 / 未决

1. **CSV 加列待用户定**：候选=设定值（vset/iset）、输出开关状态、负载工作模式。
2. **驱动提速待用户确认**：改用 `MEAS:VOLT? ALL`/`MEAS:CURR? ALL`，每轮 6 条命令减为 2 条（仅适用 SCPI 机型；IT8510 老协议单帧 0x5F 已一次回 V/I/P，无需改）。
3. **真机验证**：~~IT8510 负载~~（2026-11-02 已通）；**IT6302 电源侧仍未接过真机**，接上后先跑"通信自检"。
4. **重新打包 exe**（当前 dist 是旧版）。
5. ~~本地目录改名 `lab-console`~~（已完成：仓库直接克隆到 D:/easyCode/lab-console）。
6. 记录数据目前存内存（~4.4h），长期记录需导出；是否落盘实时写待定。
7. 远程仓库默认分支建议在 GitHub 上切为 dev。
8. IT8510 老协议设定值回读未做（set 后不回查 level）；CR 档 4000Ω 上限、CP 档实测未覆盖，待用户实际使用时确认。
9. 波特率下拉 fallback：`fillBaudSelect()` 在 model_specs 缺失时回退 [4800,9600,19200,38400]（老版本后端兼容），正常路径走型号注册表。

## 八、协作偏好（来自用户反馈）

- **视觉验收优先于数值指标**：改完 UI 必须实际截图确认，用户对可见瑕疵零容忍，习惯用截图标注问题区域反馈，回复时描述要能落到具体位置。
- 中文沟通；用户会口述需求（口语化），实现前先复述关键语义（尤其状态机类需求）以免理解偏差。
- 界面改动后必须递增 `?v=N` 并用探针/截图双重验证后再交付。
