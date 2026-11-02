/* ITECH 测试台 —— 前端逻辑（多设备版）
   仪器清单支持任意台电源/负载并存：S.view = 设备id | 'sweep'
   每台设备一个视图 section（动态生成），sidebar 每台一个条目 */

const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));

const S = {
  view: null,                    // 设备 id 或 'sweep'
  loadMode: {},                  // devId -> CC/CV/CR/CP
  combo: {},
  comboLive: {},                 // devId -> 正按组合方式接通的组合键('s12'…)或 null；与视图切换无关
  ports: [],
  cfg: {},                       // devId -> {port, baud}
  lastAlertsTs: 0,
  lastSweepKey: '',
  curveStamp: '',
  showP: { sweep: false },
  cursor: { dev: '', kind: '', idx: -1, cell: 0 },
  geom: {},                      // 'p:devId:cell' / 'l:devId' -> plot 几何
  axis: { px: 0, py: 0, lx: 0, ly: 0 },
  powerCells: {},                // devId -> [曲线单元引用]
  booted: false,
  desktop: false,
};

try {
  const c = JSON.parse(localStorage.getItem('itech.combo') || '{}');
  if (c && typeof c === 'object') S.combo = c;
} catch (e) { /* 无痕模式忽略 */ }

const MODE_META = {
  CC: { label: '负载电流', unit: 'A', min: 0.001, max: 30, step: 0.01, def: 0.5 },
  CV: { label: '负载电压', unit: 'V', min: 0.001, max: 150, step: 0.1, def: 5 },
  CR: { label: '负载电阻', unit: 'Ω', min: 0.01, max: 7500, step: 0.01, def: 100 },
  CP: { label: '负载功率', unit: 'W', min: 0.001, max: 150, step: 0.1, def: 10 },
};

/* 电源通道联合输出组合（接线按仪器手册手动完成，软件负责联动与组合读数） */
const COMBO = {
  s12: { chs: [1, 2], series: true, label: 'CH1+CH2 串联' },
  p12: { chs: [1, 2], series: false, label: 'CH1∥CH2 并联' },
  s13: { chs: [1, 3], series: true, label: 'CH1+CH3 串联' },
  s23: { chs: [2, 3], series: true, label: 'CH2+CH3 串联' },
};
/* 组合输出的总能力：[总电压上限, 总电流上限] */
const COMBO_LIMITS = { s12: [60, 3], p12: [30, 6], s13: [35, 3], s23: [35, 3] };

function comboMeta(devId) { return COMBO[S.combo[devId]] || null; }

/* 启动恢复：上次会话中正按组合方式接通的结构（无效键丢弃） */
try {
  const lv = JSON.parse(localStorage.getItem('itech.combolive') || '{}');
  if (lv && typeof lv === 'object') {
    for (const k of Object.keys(lv)) if (COMBO[lv[k]]) S.comboLive[k] = lv[k];
  }
} catch (e) { /* 无痕模式忽略 */ }

/* 当前正按组合方式接通的组合（未接通返回 null），与单路/组合视图切换无关 */
function liveComboOf(devId) { const k = S.comboLive[devId]; return k ? COMBO[k] : null; }

/* 持久化结构选择与组合接通标记（重启后恢复占用状态） */
function saveComboState() {
  try {
    localStorage.setItem('itech.combo', JSON.stringify(S.combo));
    localStorage.setItem('itech.combolive', JSON.stringify(S.comboLive));
  } catch (e) { /* 忽略 */ }
}

/* 组合输出的接线指引：跳线方式 + 负载接入 + 端子示意图 */
const WIRE_GUIDE = {
  s12: {
    label: 'CH1+CH2 串联', cap: '总电压上限 60V · 最大电流 3A',
    jumpers: ['CH1 负极(−) —跳线— CH2 正极(+)（1 根短接排）'],
    load: ['CH1 正极(+) → 负载正极', 'CH2 负极(−) → 负载负极'],
    note: '串联后总电压 = 两路设定之和（软件自动对半分压），电流取各路较小值，注意 3A 上限。',
  },
  p12: {
    label: 'CH1∥CH2 并联', cap: '电压上限 30V · 最大电流 6A',
    jumpers: ['CH1 正极(+) —跳线— CH2 正极(+)', 'CH1 负极(−) —跳线— CH2 负极(−)（共 2 根短接排）'],
    load: ['负载正极/负极接在 CH1（或 CH2）的正负极即可（两端已并联）'],
    note: '并联后两路电压保持一致（软件自动同步设定），总电流 = 两路之和（软件自动对半分配）。',
  },
  s13: {
    label: 'CH1+CH3 串联', cap: '总电压上限 35V · 最大电流 3A',
    jumpers: ['CH1 负极(−) —跳线— CH3 正极(+)（1 根短接排）'],
    load: ['CH1 正极(+) → 负载正极', 'CH3 负极(−) → 负载负极'],
    note: 'CH3 仅 5V 量程：设定总电压时 30V 通道吃满、余量给 CH3，超过 35V 会被拒绝。',
  },
  s23: {
    label: 'CH2+CH3 串联', cap: '总电压上限 35V · 最大电流 3A',
    jumpers: ['CH2 负极(−) —跳线— CH3 正极(+)（1 根短接排）'],
    load: ['CH2 正极(+) → 负载正极', 'CH3 负极(−) → 负载负极'],
    note: 'CH3 仅 5V 量程：设定总电压时 30V 通道吃满、余量给 CH3，超过 35V 会被拒绝。',
  },
};

const WIRE_COLS = { 1: 150, 2: 250, 3: 350 };   // 通道列 x（每通道竖放：上 + 下 −）
const WIRE_PY = 56, WIRE_NY = 116;               // + 行 / − 行 y

function wireSvg(mode) {
  const chs = COMBO[mode].chs;
  const has = c => chs.includes(c);
  const JUMPER = {
    s12: ['M150 116 L250 56'],
    p12: ['M150 56 L250 56', 'M150 116 L250 116'],
    s13: ['M150 116 L350 56'],
    s23: ['M250 116 L350 56'],
  };
  const LOADLINE = {
    s12: [['pos', 150, 56, 230], ['neg', 250, 116, 290]],
    p12: [['pos', 150, 56, 230], ['neg', 150, 116, 290]],
    s13: [['pos', 150, 56, 230], ['neg', 350, 116, 290]],
    s23: [['pos', 250, 56, 230], ['neg', 350, 116, 290]],
  };
  let sv = `<svg class="wire-svg" viewBox="0 0 520 230" width="520" height="230">`;
  // 端子排底板（横条：上排 + 下排 −）
  sv += `<rect x="120" y="36" width="290" height="34" rx="8" class="wt-panel"/>`;
  sv += `<rect x="120" y="96" width="290" height="34" rx="8" class="wt-panel"/>`;
  for (const c of [1, 2, 3]) {
    const x = WIRE_COLS[c], used = has(c), dim = used ? '' : ' dim';
    sv += `<text x="${x}" y="30" text-anchor="middle" class="wt-lab${dim}">CH${c}</text>`;
    sv += `<circle cx="${x}" cy="${WIRE_PY}" r="13" class="wt-p${dim}"/><text x="${x}" y="${WIRE_PY + 4}" class="wt-t${dim}">+</text>`;
    sv += `<circle cx="${x}" cy="${WIRE_NY}" r="13" class="wt-n${dim}"/><text x="${x}" y="${WIRE_NY + 4}" class="wt-t${dim}">−</text>`;
  }
  // 跳线（橙，画在端子之上）
  (JUMPER[mode] || []).forEach(d => { sv += `<path d="${d}" class="wt-jump"/>`; });
  // 到负载的引线（从端子垂直下引到负载框）
  LOADLINE[mode].forEach(([pol, x, y, lx]) => {
    sv += `<path d="M${x} ${y + 14} V 196 H ${lx}" class="wt-${pol}"/>`;
  });
  sv += `<rect x="200" y="196" width="120" height="30" rx="7" class="wt-loadbox"/>`;
  sv += `<text x="230" y="215" text-anchor="middle" class="wt-loadtxt">＋ 负载 −</text>`;
  sv += `</svg>`;
  return sv;
}

let wireCtx = null;   // 当前接线指引上下文 {mode, devId}

/* 警示条：各成员通道实时输出状态；正在输出的通道带"关闭"按钮 */
function wireWarnHtml(mode, devId) {
  const st = STATUS && STATUS.devices.find(d => d.id === devId);
  const items = COMBO[mode].chs.map(c => {
    const ch = st && st.channels[c];
    const on = !!(ch && ch.on);
    const state = on ? `输出中 ${Number(ch.v).toFixed(2)}V / ${Number(ch.i).toFixed(3)}A` : '未输出';
    return `<span class="wire-warn-item">CH${c}（${state}）${
      on ? `<button class="wire-off-btn" data-wire-off="${c}" title="关闭 CH${c} 输出">关闭</button>` : ''
    }</span>`;
  }).join('');
  return `<div class="wire-warn">`
    + `<div>开启组合输出时，以下成员通道的<b>原有输出会先被关闭</b>，随后组合输出开启；未参与组合的通道不受影响。</div>`
    + `<div class="wire-warn-list">${items}</div>`
    + `</div>`;
}

function showWireGuide(mode, devId) {
  const g = WIRE_GUIDE[mode];
  if (!g) return;
  wireCtx = { mode, devId };
  $('#wire-title').textContent = `组合输出 · ${g.label} 接线指引`;
  const steps = [
    '确认输出处于 OFF 状态（接线必须在输出关闭时进行）',
    ...g.jumpers.map(j => `跳线短接：${j}`),
    ...g.load.map(l => `负载接入：${l}`),
    `确认组合能力：${g.cap}`,
  ];
  $('#wire-body').innerHTML = `
    <div class="wire-legend">
      <span><i class="lg-jump"></i>跳线短接排</span>
      <span><i class="lg-pos"></i>正极引线</span>
      <span><i class="lg-neg"></i>负极引线</span>
    </div>
    ${wireSvg(mode)}
    <ol class="wire-steps">${steps.map(t => `<li>${t}</li>`).join('')}</ol>
    ${wireWarnHtml(mode, devId)}
    <p class="wire-note">⚠ ${g.note} 跳线排请选用能承受 3A 以上的短接排/粗导线。</p>`;
  $('#wire-mask').style.display = 'flex';
}

/* 单路卡上开启被组合占用的通道：提示需先关闭组合输出；确认后关闭组合并按单路开启该通道 */
let comboOffCtx = null;   // {devId, mode, chs}
function showComboOffDialog(devId, live, chs) {
  comboOffCtx = { devId, mode: S.comboLive[devId], chs: (chs || []).slice() };
  const names = live.chs.map(c => 'CH' + c).join('、');
  const want = (chs || []).map(c => 'CH' + c).join('、');
  $('#combooff-msg').textContent =
    `${names} 正作为「${live.label}」组合输出中，无法单独开启。`
    + `确认后将关闭该组合输出（接线可保留，仅关闭输出），并按单路开启 ${want}。`;
  $('#combooff-mask').style.display = 'flex';
}


/* 逻辑输出列表：独立=三路；组合=组合路 + 剩余独立路 */
function logicalOutputs(devId) {
  const m = comboMeta(devId);
  if (!m) {
    return [
      { id: 'ch1', label: 'CH1', chs: [1], maxV: 30, maxI: 3 },
      { id: 'ch2', label: 'CH2', chs: [2], maxV: 30, maxI: 3 },
      { id: 'ch3', label: 'CH3', chs: [3], maxV: 5, maxI: 3 },
    ];
  }
  const [maxV, maxI] = COMBO_LIMITS[S.combo[devId]];
  const rest = [1, 2, 3].filter(c => !m.chs.includes(c));
  return [
    { id: 'c' + m.chs.join(''), label: m.label, combo: m, mode: S.combo[devId], chs: m.chs.slice(), maxV, maxI },
    ...rest.map(c => ({ id: 'ch' + c, label: 'CH' + c, chs: [c], maxV: c === 3 ? 5 : 30, maxI: 3 })),
  ];
}

/* 一行历史采样（h.devs[devId]）在某个逻辑输出上的 U/I/P */
function combinedPoint(dp, o) {
  dp = dp || {};
  if (!o.combo) {
    const a = dp['ch' + o.chs[0]];
    return a ? [a[0], a[1], a[2]] : [null, null, null];
  }
  const A = dp['ch' + o.combo.chs[0]], B = dp['ch' + o.combo.chs[1]];
  if (!A || !B) return [null, null, null];
  return o.combo.series
    ? [A[0] + B[0], Math.min(A[1], B[1]), A[2] + B[2]]
    : [A[0], A[1] + B[1], A[2] + B[2]];
}

function histPayload(h, devId) {
  return (h && h.devs && h.devs[devId]) || {};
}

/* 通道卡实时值（来自设备 channels 状态而非 history） */
function cardPoint(p, o) {
  const g = c => p.channels[c] || { v: 0, i: 0, p: 0 };
  if (!o.combo) {
    const d = g(o.chs[0]);
    return [d.v, d.i, d.p];
  }
  const A = g(o.combo.chs[0]), B = g(o.combo.chs[1]);
  return o.combo.series
    ? [A.v + B.v, Math.min(A.i, B.i), A.p + B.p]
    : [A.v, A.i + B.i, A.p + B.p];
}

/* 通道卡设定值显示：串联=Σvset/同 iset；并联=同 vset/Σiset */
function cardSetpoints(p, o) {
  const g = c => p.channels[c] || { vset: 0, iset: 0 };
  if (!o.combo) {
    const d = g(o.chs[0]);
    return [d.vset, d.iset];
  }
  const A = g(o.combo.chs[0]), B = g(o.combo.chs[1]);
  return o.combo.series ? [A.vset + B.vset, A.iset] : [A.vset, A.iset + B.iset];
}

const V_PRESETS = [1.8, 3.3, 5, 8, 12, 15, 20, 24];

function cardHtml(o) {
  const pop = '<div class="v-presets">'
    + V_PRESETS.filter(v => v <= o.maxV)
      .map(v => `<button type="button" data-v="${v}">${v}</button>`).join('')
    + '</div>';
  const occupy = o.combo
    ? '<span class="occupy-tag" hidden title="成员通道正在独立输出，尚未按组合方式接通">占用</span>'
    : '<span class="occupy-tag" hidden title="该通道正作为已接通组合输出的成员被占用，需先关闭组合输出">占用</span>';
  return `<div class="ch${o.combo ? ' combo' : ''}" data-chs="${o.chs.join(',')}">
    <div class="ch-head"><span class="ch-name">${o.label}</span>
      <span style="flex:1 1 auto"></span>${occupy}
      <label class="switch xl" title="输出开关"><input type="checkbox" data-out><span></span></label></div>
    <div class="readout">
      <div class="big"><span class="val" data-k="v">0.000</span><span class="unit">V</span></div>
      <div class="big"><span class="val" data-k="i">0.000</span><span class="unit">A</span></div>
      <div class="big"><span class="val" data-k="p">0.00</span><span class="unit">W</span></div>
    </div>
    <div class="setters">
      <label class="v-field"><span>电压设定</span><input type="number" data-set="v" step="0.01" min="0" max="${o.maxV}">${pop}<i class="u">V</i></label>
      <label><span>限流</span><input type="number" data-set="i" step="0.01" min="0" max="${o.maxI}"><i class="u">A</i></label>
    </div>
    <div class="ch-foot"><span>${o.maxV}V/${o.maxI}A</span></div>
  </div>`;
}

/* ------------------------------------------------------------------ */
/* 请求                                                               */
/* ------------------------------------------------------------------ */
async function api(path, body) {
  const opt = body === undefined
    ? { method: 'GET' }
    : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) };
  const res = await fetch(path, opt);
  let data = {};
  try { data = await res.json(); } catch (e) { /* 忽略 */ }
  if (!res.ok || data.error) throw new Error(data.error || `HTTP ${res.status}`);
  return data;
}

let toastTimer = null;
function toast(msg, isErr = false) {
  const el = $('#toast');
  el.textContent = msg;
  el.className = 'toast show' + (isErr ? ' err' : '');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.className = 'toast'; }, isErr ? 6000 : 3400);
}

/* ------------------------------------------------------------------ */
/* 绘图                                                               */
/* ------------------------------------------------------------------ */
function niceNum(range, round) {
  if (range <= 0 || !isFinite(range)) return 1;
  const exp = Math.floor(Math.log10(range));
  const f = range / Math.pow(10, exp);
  let nf;
  if (round) nf = f < 1.5 ? 1 : f < 3 ? 2 : f < 7 ? 5 : 10;
  else nf = f <= 1 ? 1 : f <= 2 ? 2 : f <= 5 ? 5 : 10;
  return nf * Math.pow(10, exp);
}

function makeTicks(min, max, count = 5) {
  if (!isFinite(min) || !isFinite(max)) { min = 0; max = 1; }
  if (max - min < 1e-9) { max = min + 1; }
  const raw = (max - min) / Math.max(1, count - 1);
  const step = niceNum(raw, true);
  const lo = Math.floor(min / step) * step;
  const hi = Math.ceil(max / step) * step;
  const vals = [];
  for (let v = lo; v <= hi + step * 0.5; v += step) vals.push(+v.toFixed(10));
  return { lo, hi, vals, step };
}

/* 按可用高度选刻度密度：间距小于 gapPx 就减一档，避免三段落里只剩两个数 */
function fitTicks(maxVal, ph, gapPx) {
  let best = null;
  for (let c = 6; c >= 3; c--) {
    const t = makeTicks(0, maxVal, c);
    const span = t.hi - t.lo;
    const px = span > 0 ? ph * t.step / span : ph;
    if (px >= (gapPx || 26)) { best = t; break; }
    best = t;
  }
  return best || makeTicks(0, maxVal, 3);
}

function fmt(v, step) {
  const a = Math.abs(v);
  if (a >= 1000) return v.toFixed(0);
  if (step >= 1) return v.toFixed(0);
  if (step >= 0.1) return v.toFixed(1);
  if (step >= 0.01) return v.toFixed(2);
  if (step >= 0.001) return v.toFixed(3);
  // 更小的步长（mA/µA 级电流轴等）：按步长量级给足位数，固定位数不去尾零，
  // 保证同轴标签位数一致、相邻标签间隔均匀可读
  const d = Math.min(6, Math.max(3, 1 - Math.floor(Math.log10(step))));
  return v.toFixed(d);
}

/* 格线反算标签专用：值不是刻度步长的整数倍（按格线高度反算），
   位数取"相邻标签至少差 1 位"（ceil(-log10(step))+1），固定位数去歧义 */
function fmtPrec(v, step) {
  const a = Math.abs(v);
  if (a >= 1000) return v.toFixed(0);
  if (!isFinite(step) || step <= 0) return fmt(v, 0.001);
  const d = Math.min(7, Math.max(3, Math.ceil(-Math.log10(step)) + 1));
  return v.toFixed(d);
}

/* ------------------------------------------------------------------ */
/* 绘图：分条小多图 —— 每段一种物理量，共享一根时间轴                   */
/* ------------------------------------------------------------------ */
function plot(canvas, cfg) {
  const pal = themePal();
  const dpr = window.devicePixelRatio || 1;
  const rect = canvas.getBoundingClientRect();
  const W = Math.round(rect.width);
  const H = Math.round(rect.height);
  if (W < 60 || H < 60) return null;
  if (canvas.width !== Math.round(W * dpr) || canvas.height !== Math.round(H * dpr)) {
    canvas.width = Math.round(W * dpr);
    canvas.height = Math.round(H * dpr);
  }
  const ctx = canvas.getContext('2d');
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, W, H);

  const xs = cfg.xs || [];
  const panels = (cfg.panels || []).filter(p => p && p.series && p.series.length);
  if (!panels.length || xs.length < 2) return null;

  const n = panels.length;
  const anyRight = panels.some(p => p.series.some(s => s.axis === 'right'));
  const anyR2 = panels.some(p => p.series.some(s => s.axis === 'right2'));
  const padL = 42;
  // 右侧只有一列刻度（功率上/电流下同列左对齐），64px 足够 8 位数字
  const padR = (anyRight || anyR2) ? 64 : 10;
  const padT = 16;   // 顶部留白放轴单位，避免与最上面的刻度值重叠
  const padB = 18;
  const gap = cfg.gap != null ? cfg.gap : 12;
  const band = 13;
  const pw = W - padL - padR;
  const ph = (H - padT - padB - band * n - gap * (n - 1)) / n;
  if (pw < 50 || ph < 20) return null;

  const xmin = xs[0];
  const xmax = xs[xs.length - 1];
  const xSpan = Math.max(xmax - xmin, 1e-9);
  const X = v => padL + (v - xmin) / xSpan * pw;
  const xt = makeTicks(xmin, xmax, Math.max(2, Math.min(8, Math.round(pw / 92))));
  const bottomY = padT + n * band + n * ph + gap * (n - 1);
  const stride = Math.max(1, Math.ceil(xs.length / Math.max(240, pw * 2)));

  ctx.font = '11.5px "JetBrains Mono", Consolas, monospace';
  ctx.textBaseline = 'middle';

  // 贯通竖格线
  ctx.strokeStyle = pal.grid;
  ctx.lineWidth = 1;
  xt.vals.forEach(v => {
    if (v < xmin - 1e-9 || v > xmax + 1e-9) return;
    const x = Math.round(X(v)) + 0.5;
    ctx.beginPath();
    ctx.moveTo(x, padT + band - 4);
    ctx.lineTo(x, bottomY);
    ctx.stroke();
  });

  // 轴单位：画在绘图区上缘之外（padT-4），与刻度值完全分离
  // 右侧功率/电流刻度合并为一列，单位也随之合并（如 "A/W"）
  const units = cfg.units || {};
  const rUnits = [(units.r && anyRight) ? units.r : '', (units.r2 && anyR2) ? units.r2 : '']
    .filter(Boolean).join('/');
  if (units.l || rUnits) {
    ctx.fillStyle = pal.unit;
    ctx.font = '10.5px "JetBrains Mono", Consolas, monospace';
    if (units.l) { ctx.textAlign = 'right'; ctx.fillText(units.l, padL - 6, padT - 4); }
    if (rUnits) { ctx.textAlign = 'left'; ctx.fillText(rUnits, padL + pw + 7, padT - 4); }
    ctx.font = '11.5px "JetBrains Mono", Consolas, monospace';
  }

  const geoms = [];
  panels.forEach((p, idx) => {
    const labelY = padT + idx * (band + ph + gap);
    const y0 = labelY + band;
    const left = p.series.filter(s => s.axis !== 'right' && s.axis !== 'right2');
    const right = p.series.filter(s => s.axis === 'right');
    const r2 = p.series.filter(s => s.axis === 'right2');

    const maxOf = arr => {
      let mx = -Infinity;
      arr.forEach(s => s.vals.forEach(v => {
        if (v != null && isFinite(v) && v > mx) mx = v;
      }));
      return isFinite(mx) && mx > 0 ? mx : 1;
    };
    const lt = fitTicks(cfg.ymax > 0 ? cfg.ymax : maxOf(left) * 1.16, ph, 26);
    const rt = fitTicks(maxOf(right) * 1.16, ph, 26);
    const r2t = r2.length ? fitTicks(maxOf(r2) * 1.16, ph, 26) : null;
    const yT = y0 + 5;
    const yB = y0 + ph - 5;
    const LY = v => yB - (v - lt.lo) / (lt.hi - lt.lo) * (yB - yT);
    const RY = v => yB - (v - rt.lo) / (rt.hi - rt.lo) * (yB - yT);
    const R2Y = r2t ? v => yB - (v - r2t.lo) / (r2t.hi - r2t.lo) * (yB - yT) : RY;

    if (p.label) {
      ctx.fillStyle = pal.label;
      ctx.textAlign = 'left';
      ctx.font = '10.5px "JetBrains Mono", Consolas, monospace';
      ctx.fillText(p.label, padL + 1, labelY + 6.5);
      ctx.font = '11.5px "JetBrains Mono", Consolas, monospace';
    }
    if (p.ru) {
      ctx.fillStyle = pal.unit;
      ctx.textAlign = 'right';
      ctx.font = '10.5px "JetBrains Mono", Consolas, monospace';
      ctx.fillText(p.ru, padL + pw, labelY + 6.5);
      ctx.font = '11.5px "JetBrains Mono", Consolas, monospace';
    }

    // 横格线（按左轴格线 y）+ 左刻度
    ctx.strokeStyle = pal.hline;
    ctx.lineWidth = 1;
    ctx.fillStyle = p.lc || pal.label;
    ctx.textAlign = 'right';
    lt.vals.forEach(v => {
      const y = Math.round(LY(v)) + 0.5;
      if (y < yT - 2 || y > yB + 2) return;
      ctx.beginPath();
      ctx.moveTo(padL, y);
      ctx.lineTo(padL + pw, y);
      ctx.stroke();
      ctx.fillText(fmt(v, lt.step), padL - 6, y);
    });
    // 右侧刻度与格线严格对齐：同一列（x=padL+pw+7 左对齐），
    // 右侧刻度：像左轴一样取整齐的整数间隔（0.001/0.002…），画在自己轴的真实高度，
    // 同列左对齐（功率橙/电流青）；右缘画同色短刻度线作为数值锚点；
    // 两组标签位置太近时上下错开（功率上移/电流下移），保证两组都可见
    if (right.length || r2.length) {
      const curYs = [];
      const powYs = [];
      if (right.length) {
        rt.vals.forEach(v => {
          const y = Math.round(RY(v));
          if (y >= yT - 2 && y <= yB + 2) curYs.push([v, y]);
        });
      }
      if (r2.length) {
        r2t.vals.forEach(v => {
          const y = Math.round(R2Y(v));
          if (y >= yT - 2 && y <= yB + 2) powYs.push([v, y]);
        });
      }
      const near = (y, arr) => arr.some(([, yy]) => Math.abs(yy - y) < 11);
      // 右缘短刻度线（4px，同色）：数值的视觉锚点
      ctx.lineWidth = 1;
      const tick = (y, color) => {
        ctx.strokeStyle = color;
        ctx.beginPath();
        ctx.moveTo(padL + pw - 4, y + 0.5);
        ctx.lineTo(padL + pw, y + 0.5);
        ctx.stroke();
      };
      curYs.forEach(([, y]) => tick(y, p.rc || pal.label));
      powYs.forEach(([, y]) => tick(y, p.wc || pal.label));
      ctx.textAlign = 'left';
      powYs.forEach(([v, y]) => {
        ctx.fillStyle = p.wc || pal.label;
        ctx.fillText(fmt(v, r2t.step), padL + pw + 7, near(y, curYs) ? y - 9 : y);
      });
      curYs.forEach(([v, y]) => {
        ctx.fillStyle = p.rc || pal.label;
        ctx.fillText(fmt(v, rt.step), padL + pw + 7, near(y, powYs) ? y + 9 : y);
      });
    }

    // 边框
    ctx.strokeStyle = pal.frame;
    ctx.beginPath();
    ctx.moveTo(padL + 0.5, y0);
    ctx.lineTo(padL + 0.5, y0 + ph);
    ctx.lineTo(padL + pw, y0 + ph);
    ctx.stroke();

    // 曲线
    p.series.forEach(s => {
      const YF = s.axis === 'right' ? RY : s.axis === 'right2' ? R2Y : LY;
      ctx.strokeStyle = s.color;
      ctx.lineWidth = s.width || 1.6;
      if (s.dash) ctx.setLineDash(s.dash);
      ctx.beginPath();
      let started = false;
      for (let i = 0; i < xs.length; i += stride) {
        const v = s.vals[i];
        if (v == null || !isFinite(v)) { started = false; continue; }
        const x = X(xs[i]);
        const y = YF(v);
        if (!started) { ctx.moveTo(x, y); started = true; }
        else ctx.lineTo(x, y);
      }
      ctx.stroke();
      ctx.setLineDash([]);
    });

    geoms.push({ p, LY, RY, R2Y });
  });

  // 横轴刻度（底部）
  ctx.fillStyle = '#657383';
  ctx.textAlign = 'center';
  const xUnit = cfg.xUnit || ' s';
  xt.vals.forEach(v => {
    const x = X(v);
    if (x < padL - 1 || x > padL + pw + 1) return;
    const label = fmt(v, xt.step) + xUnit;
    const tw = ctx.measureText(label).width;
    if (x - tw / 2 < padL - 14 || x + tw / 2 > padL + pw + 20) return;
    ctx.fillText(label, x, bottomY + 9);
  });

  return { ctx, W, H, padL, pw, X, xs, xmin, xmax, top: padT + band - 4, bottom: bottomY, geoms };
}

/* 十字线：竖直游标 + 各段交点；box 为真时才画读数框 */
/* 画布主题色板：随 html.light 切换 */
function themePal() {
  return document.documentElement.classList.contains('light') ? {
    grid: '#e4eaf1',        // 贯通竖格线
    unit: '#8393a4',        // 轴单位
    label: '#5b6b7c',       // 段标签 / 刻度辅色
    tick: '#5b6b7c',        // 刻度数字（按轴色着色时仍用轴色）
    hline: '#e0e7ef',       // 横格线
    frame: '#cfd9e4',       // 图区边框
    dotRing: '#ffffff',     // 数据点描边
    cursorBg: 'rgba(255, 255, 255, .97)',
    cursorLine: '#b9c8d8',
    cursorTime: '#5b6b7c',
    cursorName: '#7c8c9c',
    cursorRow: '#5b6b7c',
    cursorVal: '#1d2733',
  } : {
    grid: '#141d27', unit: '#657383', label: '#8fa0b3', tick: '#657383',
    hline: '#1a2531', frame: '#1e2836', dotRing: '#0a0e13',
    cursorBg: 'rgba(10, 14, 19, .95)', cursorLine: '#26364a',
    cursorTime: '#8b9db0', cursorName: '#5d6f82', cursorRow: '#7e8fa1', cursorVal: '#dbe7f3',
  };
}

function drawCursor(g, idx, box) {
  const pal = themePal();
  if (!g || idx < 0 || idx >= g.xs.length) return;
  const ctx = g.ctx;
  const x = g.X(g.xs[idx]);

  ctx.save();
  ctx.strokeStyle = 'rgba(126, 160, 196, .5)';
  ctx.setLineDash([3, 3]);
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(Math.round(x) + 0.5, g.top);
  ctx.lineTo(Math.round(x) + 0.5, g.bottom);
  ctx.stroke();
  ctx.setLineDash([]);

  g.geoms.forEach(({ p, LY, RY, R2Y }) => {
    p.series.forEach(s => {
      const v = s.vals[idx];
      if (v == null || !isFinite(v)) return;
      const y = (s.axis === 'right' ? RY : s.axis === 'right2' ? R2Y : LY)(v);
      ctx.beginPath();
      ctx.arc(x, y, 3.1, 0, Math.PI * 2);
      ctx.fillStyle = s.color;
      ctx.fill();
      ctx.strokeStyle = pal.dotRing;
      ctx.lineWidth = 1.6;
      ctx.stroke();
    });
  });

  if (!box) { ctx.restore(); return; }

  // 读数框
  const names = (g.geoms[0] && g.geoms[0].p.names) || [];
  const cols = Math.max(1, names.length);
  const rowH = 15;
  const colW = 54;
  const labW = 30;
  const padX = 8;
  const bw = padX * 2 + labW + colW * cols;
  const bh = rowH * (g.geoms.length + 2) + 6;
  let bx = x < g.padL + g.pw / 2 ? x + 14 : x - bw - 14;
  if (bx < 4) bx = x + 14;
  if (bx + bw > g.W - 4) bx = Math.max(4, x - bw - 14);
  const by = g.top + 2;

  ctx.beginPath();
  const r = 6;
  ctx.moveTo(bx + r, by);
  ctx.arcTo(bx + bw, by, bx + bw, by + bh, r);
  ctx.arcTo(bx + bw, by + bh, bx, by + bh, r);
  ctx.arcTo(bx, by + bh, bx, by, r);
  ctx.arcTo(bx, by, bx + bw, by, r);
  ctx.closePath();
  ctx.fillStyle = pal.cursorBg;
  ctx.fill();
  ctx.strokeStyle = pal.cursorLine;
  ctx.lineWidth = 1;
  ctx.stroke();

  ctx.font = '10.5px "JetBrains Mono", Consolas, monospace';
  ctx.textBaseline = 'middle';
  ctx.textAlign = 'left';
  ctx.fillStyle = pal.cursorTime;
  ctx.fillText(g.xs[idx].toFixed(1) + ' s', bx + padX, by + 7 + rowH * 0.5);

  ctx.fillStyle = pal.cursorName;
  names.forEach((nm, i) => {
    ctx.fillStyle = g.geoms[0].p.series[i] ? g.geoms[0].p.series[i].color : pal.cursorName;
    ctx.fillText(nm, bx + padX + labW + colW * i, by + 7 + rowH * 1.5);
  });

  g.geoms.forEach(({ p }, ri) => {
    const yy = by + 7 + rowH * (ri + 2) + rowH * 0.5;
    ctx.fillStyle = pal.cursorRow;
    ctx.fillText((p.label || '').split(' ')[0], bx + padX, yy);
    ctx.fillStyle = pal.cursorVal;
    p.series.forEach((s, ci) => {
      const v = s.vals[idx];
      // 高精度：电压/电流/功率各按量纲取位（fmtV/fmtI/fmtP 小值保留 3 位有效数字）
      const t = v == null || !isFinite(v) ? '—'
        : (ci === 0 ? fmtV(v) : ci === 1 ? fmtI(v) : fmtP(v));
      ctx.fillText(t, bx + padX + labW + colW * ci, yy);
    });
  });

  ctx.restore();
}

/* ------------------------------------------------------------------ */
/* 视图 / 导航                                                         */
/* ------------------------------------------------------------------ */
function deviceState(meta) {
  if (meta && meta.error) return { cls: 'err', text: '通信异常' };
  if (meta && meta.connected && meta.sim) return { cls: 'sim', text: '模拟中' };
  if (meta && meta.connected) return { cls: 'on', text: '已连接' };
  return { cls: '', text: '未连接' };
}

function curDev() {
  return STATUS ? STATUS.devices.find(d => d.id === S.view) : null;
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

/* 侧栏仪器条目 */
function devEntryHtml(d) {
  // 副行: 紧凑规格 (侧栏窄, 完整信息在 title 提示里)
  let spec, tip;
  if (d.role === 'power') {
    const ch = (d.channels && Object.keys(d.channels).length) || 3;
    spec = `${ch}通道 · 30V/3A`;
    tip = `${ch} 通道直流电源 · CH1/CH2 30V/3A · CH3 5V/3A`;
  } else if (d.range) {
    spec = `${d.range.max_v}V ${d.range.max_i}A ${d.range.max_p}W`;
    tip = `直流电子负载 · 量程 ${d.range.max_v}V / ${d.range.max_i}A / ${d.range.max_p}W · CC/CV/CR/CP`;
  } else {
    spec = ''; tip = '';
  }
  return `<button type="button" class="dev" data-dev="${d.id}" data-role="${d.role}" draggable="true" title="${tip}<br>点击打开视图，拖动可调整顺序">
    <span class="dev-ico">${d.role === 'power' ? 'P' : 'L'}</span>
    <span class="dev-txt">
      <b><span class="dev-name">${escapeHtml(d.name)}</span><i class="dev-model">${d.model}</i></b>
      <em>${spec}</em>
    </span>
    <span class="dev-state"><i class="dot"></i><s>未连接</s></span>
    <span class="dev-del" data-del="${d.id}" title="从清单删除该仪器">×</span>
  </button>`;
}
function bindDelBadge(d) {
  let timer = null;
  const dev = d.closest('.dev');
  const reset = () => { d.classList.remove('arm'); d.textContent = '×'; dev.classList.remove('armed'); };
  d.onclick = async ev => {
    ev.stopPropagation();
    if (!d.classList.contains('arm')) {
      d.classList.add('arm');
      d.textContent = '删除';
      dev.classList.add('armed');
      timer = setTimeout(reset, 2600);
      return;
    }
    clearTimeout(timer);
    reset();
    try { await api('/api/instrument/remove', { dev: d.dataset.del }); toast('已从清单删除仪器'); }
    catch (e) { toast(e.message, true); }
  };
  // 鼠标移出仪器条目即取消删除（iOS 通知式）
  dev.addEventListener('mouseleave', () => { if (dev.classList.contains('armed')) { clearTimeout(timer); reset(); } });
}

/* 侧栏仪器条目拖动排序 */
function bindDevDrag(el) {
  el.addEventListener('dragstart', ev => {
    el.classList.add('dragging');
    ev.dataTransfer.effectAllowed = 'move';
    try { ev.dataTransfer.setData('text/plain', el.dataset.dev); } catch (e) { /* 忽略 */ }
  });
  el.addEventListener('dragend', () => {
    el.classList.remove('dragging');
    $$('#dev-list .dev').forEach(x => x.classList.remove('drop-above', 'drop-below'));
  });
  el.addEventListener('dragover', ev => {
    const from = document.querySelector('#dev-list .dev.dragging');
    if (!from || from === el) return;
    ev.preventDefault();
    ev.dataTransfer.dropEffect = 'move';
    const r = el.getBoundingClientRect();
    const below = ev.clientY > r.top + r.height / 2;
    el.classList.toggle('drop-above', !below);
    el.classList.toggle('drop-below', below);
  });
  el.addEventListener('dragleave', () => el.classList.remove('drop-above', 'drop-below'));
  el.addEventListener('drop', async ev => {
    ev.preventDefault();
    const r = el.getBoundingClientRect();
    const below = ev.clientY > r.top + r.height / 2;
    el.classList.remove('drop-above', 'drop-below');
    const fromEl = document.querySelector('#dev-list .dev.dragging');
    if (!fromEl || fromEl === el || !STATUS) return;
    const from = fromEl.dataset.dev, to = el.dataset.dev;
    const ids = STATUS.devices.map(d => d.id).filter(i => i !== from);
    const idx = ids.indexOf(to);
    ids.splice(below ? idx + 1 : idx, 0, from);
    try { await api('/api/instrument/reorder', { ids }); toast('已调整仪器顺序'); }
    catch (e) { toast(e.message, true); }
  });
}

let navStamp = '';
function renderNav(st) {
  const list = $('#dev-list');
  const stamp = st.devices.map(d => `${d.id}:${d.model}:${d.name}:${d.connected ? 1 : 0}${d.sim ? 's' : ''}`).join('|');
  if (navStamp !== stamp) {
    navStamp = stamp;
    list.innerHTML = st.devices.map(devEntryHtml).join('');
    $$('#dev-list .dev').forEach(el => {
      el.onclick = () => setView(el.dataset.dev);
      bindDevDrag(el);
    });
    $$('#dev-list .dev-del').forEach(bindDelBadge);
    st.devices.forEach(d => {
      if (!S.cfg[d.id]) S.cfg[d.id] = { port: '', baud: 0 };
      // 已连接设备: 用后端报告的实际端口/波特率同步显示 (sim 除外)
      if (d.connected && !d.sim && d.port) {
        S.cfg[d.id].port = d.port;
        if (d.baud) S.cfg[d.id].baud = d.baud;
      }
      if (S.loadMode[d.id] == null) S.loadMode[d.id] = 'CC';
      if (S.combo[d.id] == null) S.combo[d.id] = 'none';
      if (S.comboLive[d.id] == null) S.comboLive[d.id] = null;
    });
    if (S.view !== 'sweep' && !st.devices.some(d => d.id === S.view)) {
      setView(st.devices.length ? st.devices[0].id : 'sweep');
    }
  }
  st.devices.forEach(d => {
    const el = list.querySelector(`.dev[data-dev="${d.id}"]`);
    if (!el) return;
    const s = deviceState(d);
    // 保留 armed（删除确认中），避免 700ms 轮询重建 className 时丢失
    el.className = 'dev' + (el.classList.contains('armed') ? ' armed' : '')
      + (S.view === d.id ? ' sel' : '') + (s.cls ? ' ' + s.cls : '');
    el.querySelector('.dev-state s').textContent = s.text;
  });

  // 扫描导航状态
  const nav = $('.dev[data-role="sweep"]');
  if (nav) {
    const sw = st.sweep;
    const pct = sw.total ? Math.round(sw.progress / sw.total * 100) : 0;
    const s = nav.querySelector('.dev-state s');
    if (sw.running) s.textContent = `${pct}%`;
    else if (sw.points.length) s.textContent = '已完成';
    else s.textContent = '就绪';
    nav.classList.toggle('sel', S.view === 'sweep');
    nav.classList.toggle('running', !!sw.running);
    nav.classList.toggle('done', !sw.running && !!sw.points.length);
  }

  $('#pills').innerHTML = st.devices.map(d => {
    const s = deviceState(d);
    return `<div class="pill ${s.cls}"><span class="dot"></span>${escapeHtml(d.name)} <b>${s.text}</b></div>`;
  }).join('');
}

function setView(id) {
  const dev = STATUS ? STATUS.devices.find(d => d.id === id) : null;
  if (id !== 'sweep' && !dev) return;
  const changed = S.view !== id;
  S.view = id;

  $$('.view').forEach(v => v.classList.toggle('active', v.dataset.view === id));
  $$('.dev').forEach(el => el.classList.toggle('sel',
    el.dataset.dev === id || (id === 'sweep' && el.dataset.role === 'sweep')));

  const isDev = !!dev;
  $('#dev-cfg').style.display = isDev ? '' : 'none';
  $('#link-cfg').style.display = isDev ? 'none' : '';
  $('#ttl-hint').style.display = isDev ? '' : 'none';

  if (isDev) {
    $('#cfg-title').textContent = `${dev.name} · ${dev.model} · 连接设置`;
    const mf = $('#dev-model-field');
    mf.style.display = dev.role === 'load' ? '' : 'none';
    if (dev.role === 'load') {
      const sel = $('#dev-model');
      sel.innerHTML = ((STATUS.models || {}).load || []).map(m => `<option>${m}</option>`).join('');
      sel.value = dev.model;
    }
    S.cfg[id] = S.cfg[id] || { port: '', baud: 0 };
    fillBaudSelect();
    fillPortSelect();
  } else if (STATUS) {
    renderLink(STATUS);
  }

  if (changed) {
    // 视图刚显示出来，等一帧拿到真实尺寸再画
    S.curveStamp = '';
    S.lastSweepKey = '';
    requestAnimationFrame(() => {
      if (!STATUS) return;
      if (isDev) renderCurves(STATUS); else renderSweep(STATUS);
    });
  }
}

/* 联动视图左侧：所有设备在线状态 */
function renderLink(st) {
  const box = $('#link-rows');
  if (!box) return;
  const stamp = st.devices.map(d => d.id + (d.connected ? '1' : '0')).join('|');
  if (box.dataset.stamp !== stamp) {
    box.dataset.stamp = stamp;
    box.innerHTML = st.devices.map(d =>
      `<div class="lk-row" data-lk="${d.id}"><i class="dot"></i>
       <div class="lk-txt"><b>${escapeHtml(d.name)} · ${d.model}</b><em></em></div>
       <button type="button" class="tiny ghost lk-go" data-go="${d.id}">去连接</button></div>`).join('');
    $$('.lk-go', box).forEach(b => { b.onclick = () => setView(b.dataset.go); });
  }
  st.devices.forEach(d => {
    const row = box.querySelector(`.lk-row[data-lk="${d.id}"]`);
    if (!row) return;
    const s = deviceState(d);
    row.className = 'lk-row' + (s.cls ? ' ' + s.cls : '');
    row.querySelector('.lk-txt em').textContent = s.text;
  });
}

/* ------------------------------------------------------------------ */
/* 设备面板（动态 section）                                            */
/* ------------------------------------------------------------------ */
const PX_OPTIONS = [30, 60, 120, 300, 600];
const PY_POWER_OPTIONS = [2, 5, 8, 10, 15, 20, 24, 30];
const PY_LOAD_OPTIONS = [5, 10, 20, 30, 50, 100, 150];

function axisSelectHtml(key, title, opts, unit) {
  return `<label class="ax-ctl">${key.startsWith('p') && key === 'px' || key === 'lx' ? '横轴' : '纵轴'}
    <select data-axis="${key}" title="${title}">
      <option value="0" selected>自动</option>
      ${opts.map(v => `<option value="${v}">${v} ${unit}</option>`).join('')}
    </select>
  </label>`;
}

function powerPanelHtml(d) {
  return `
      <div class="view-head">
        <h2>直流电源 <span class="model">${d.model}</span></h2>
        <span class="badge dev-idn">未连接</span>
        <div class="spacer"></div>
        <div class="combo-line" title="通道联合输出：接线按仪器手册手动完成；切换时受影响的通道会先关断">
          <div class="combo-seg">
            <button type="button" class="seg-btn sel" data-c="none">单路</button>
            <button type="button" class="seg-btn" data-c="combo">组合</button>
          </div>
          <select class="combo-mode-sel" data-combo-mode style="display:none" title="选择组合方式">
            <option value="s12" selected>CH1+CH2 串联</option>
            <option value="p12">CH1∥CH2 并联</option>
            <option value="s13">CH1+CH3 串联</option>
            <option value="s23">CH2+CH3 串联</option>
          </select>
        </div>
        <button class="tiny" data-act="all-on">全部 ON</button>
        <button class="tiny" data-act="all-off">全部 OFF</button>
      </div>

      <div class="ch-grid"></div>

      <div class="power-sum">
        <span>总输出功率</span><b class="sum-total">0.00</b><span>W</span>
        <span class="sep"></span>
        <span>输出中 <b class="oncount">0</b><span class="ontotal"> / 3 路</span></span>
        <span class="combo-info"></span>
      </div>

      <div class="rec-zone">
        <button class="tiny" data-act="rec" title="选择通道与文件后开始记录，结束记录时导出 CSV">开始记录</button>
        <div class="rec-bar" style="display:none"></div>
      </div>

      <div class="curves">
        <div class="curves-head">
          <h3>三通道曲线 · 每通道一图 (U / I / W)</h3>
          <div class="legend">
            <span class="ls"><i class="s-solid" style="border-top-color:#4f9cf9"></i>电压 V</span>
            <span class="ls"><i class="s-solid" style="border-top-color:#2dd4bf"></i>电流 A</span>
            <span class="ls"><i class="s-solid" style="border-top-color:#f0a83c"></i>功率 W</span>
          </div>
          <div class="spacer"></div>
          ${axisSelectHtml('px', '横轴时间窗口：自动=按缓存最长时间显示', PX_OPTIONS, 's')}
          ${axisSelectHtml('py', '纵轴电压满量程：自动=按数据最大值分度；电流/功率轴始终自动', PY_POWER_OPTIONS, 'V')}
          <span class="badge curve-win">最近 0 s</span>
          <button class="tiny ghost" data-act="clear-curves">清空</button>
        </div>
        <div class="curve-row"></div>
        <div class="cell-drag" title="拖动同时调整数据表高度"></div>
        <div class="curve-row table-row"></div>
      </div>`;
}

function loadPanelHtml(d) {
  return `
      <div class="view-head">
        <h2>电子负载 <span class="model model-tag">${d.model}</span></h2>
        <span class="badge dev-idn">未连接</span>
        <div class="spacer"></div>
      </div>

      <div class="load-top">
        <div class="ch load-ch">
          <div class="ch-head">
            <span class="ch-name card-name">电子负载 · ${d.model}</span>
            <label class="switch xl" title="输出开关"><input type="checkbox" data-load-input><span></span></label>
          </div>
          <div class="readout">
            <div class="big"><span class="val rv">0.000</span><span class="unit">V</span></div>
            <div class="big"><span class="val ri">0.000</span><span class="unit">A</span></div>
            <div class="big"><span class="val rp">0.00</span><span class="unit">W</span></div>
          </div>
          <div class="setters">
            <div class="mode-group">
              <button type="button" data-mode="CC" class="sel">CC</button>
              <button type="button" data-mode="CV">CV</button>
              <button type="button" data-mode="CR">CR</button>
              <button type="button" data-mode="CP">CP</button>
            </div>
            <label class="field" style="margin:0">
              <span class="value-label">负载电流</span>
              <input type="number" class="load-value" step="0.01" min="0" value="0.5">
              <i class="u unit-tag">A</i>
            </label>
          </div>
          <div class="ch-foot"><span class="range-tag">量程 —</span><span class="state-tag">—</span></div>
        </div>
      </div>

      <div class="rec-zone">
        <button class="tiny" data-act="rec" title="选择文件后开始记录，结束记录时导出 CSV">开始记录</button>
        <div class="rec-bar" style="display:none"></div>
      </div>

      <div class="curves">
        <div class="curves-head">
          <h3>负载曲线 · U / I / W</h3>
          <div class="legend">
            <span class="ls"><i class="s-solid" style="border-top-color:#4f9cf9"></i>电压 V</span>
            <span class="ls"><i class="s-solid" style="border-top-color:#2dd4bf"></i>电流 A</span>
            <span class="ls"><i class="s-solid" style="border-top-color:#f0a83c"></i>功率 W</span>
          </div>
          <div class="spacer"></div>
          ${axisSelectHtml('lx', '横轴时间窗口：自动=按缓存最长时间显示', PX_OPTIONS, 's')}
          ${axisSelectHtml('ly', '电压轴满量程：自动=按数据最大值分度；电流/功率轴始终自动', PY_LOAD_OPTIONS, 'V')}
          <span class="badge curve-win">最近 0 s</span>
          <button class="tiny ghost" data-act="clear-curves">清空</button>
        </div>
        <div class="curve-single">
          <canvas></canvas>
          <div class="empty-tip">连接设备后开始采集</div>
        </div>
        <div class="cell-drag" title="拖动调整表格高度"></div>
        <div class="cell-table load-table">
          <table>
            <thead><tr><th>时间 s</th><th>U/V</th><th>I/A</th><th>P/W</th></tr></thead>
            <tbody><tr><td colspan="4" class="td-empty">—</td></tr></tbody>
          </table>
        </div>
      </div>`;
}

let panelStamp = '';
function ensurePanels(st) {
  const stamp = st.devices.map(d => `${d.id}:${d.model}:${d.role}`).join('|');
  if (panelStamp === stamp) return;
  panelStamp = stamp;
  $$('.view[data-view]').forEach(v => {
    if (v.dataset.view === 'sweep') return;
    if (!st.devices.some(d => d.id === v.dataset.view)) v.remove();
  });
  st.devices.forEach(d => {
    let sec = $('#view-' + d.id);
    if (!sec) {
      sec = document.createElement('section');
      sec.className = 'view';
      sec.dataset.view = d.id;
      sec.id = 'view-' + d.id;
      sec.innerHTML = `<div class="panel vpanel">${d.role === 'power' ? powerPanelHtml(d) : loadPanelHtml(d)}</div>`;
      $('main').appendChild(sec);
    } else {
      const tag = $('.view-head .model', sec);
      if (tag) tag.textContent = d.model;
    }
  });
  S.curveStamp = '';
  S.lastSweepKey = '';
}

/* ------------------------------------------------------------------ */
/* 渲染：面板实时值                                                    */
/* ------------------------------------------------------------------ */
function renderPowerPanel(st, d) {
  const sec = $('#view-' + d.id);
  if (!sec) return;
  const grid = $('.ch-grid', sec);
  const outs = logicalOutputs(d.id);
  const stamp = (S.combo[d.id] || 'none') + ':' + (d.connected ? '1' : '0');
  if (grid.dataset.stamp !== stamp) {
    grid.dataset.stamp = stamp;
    grid.style.gridTemplateColumns = `repeat(${outs.length}, minmax(0, 1fr))`;
    grid.innerHTML = outs.map(cardHtml).join('');
  }
  let total = 0, onCount = 0;
  const live = liveComboOf(d.id);
  outs.forEach((o, k) => {
    const card = grid.children[k];
    if (!card) return;
    const membersOn = o.chs.filter(c => d.channels[c] && d.channels[c].on);
    // 占用/接通判定：
    //  组合卡：当前结构已按组合接通（comboLive=本结构）→ ON；成员有独立输出但未接通 → 占用
    //  单路卡：通道正作为已接通组合的成员 → 占用 + OFF；否则按通道实际状态
    let engaged, occupied, on;
    if (o.combo) {
      engaged = S.comboLive[d.id] === o.mode;
      occupied = !engaged && membersOn.length > 0;
      on = engaged && membersOn.length === o.chs.length;
    } else {
      const c = o.chs[0];
      occupied = !!(live && live.chs.includes(c) && d.channels[c] && d.channels[c].on);
      on = !occupied && membersOn.length === 1;
    }
    const [v, i, pw] = cardPoint(d, o);
    card.classList.toggle('active', on);
    card.querySelector('[data-k="v"]').textContent = Number(v).toFixed(3);
    card.querySelector('[data-k="i"]').textContent = fmtI(i);
    card.querySelector('[data-k="p"]').textContent = fmtP(pw);

    const occ = card.querySelector('.occupy-tag');
    if (occ) occ.hidden = !occupied;

    const out = card.querySelector('[data-out]');
    if (out && !out.matches(':focus')) out.checked = on;
    if (out) out.disabled = !d.connected;

    const vi = card.querySelector('[data-set="v"]');
    const ii = card.querySelector('[data-set="i"]');
    const [vs, is] = cardSetpoints(d, o);
    if (vi && document.activeElement !== vi) vi.value = Number(vs).toFixed(2);
    if (ii && document.activeElement !== ii) ii.value = Number(is).toFixed(3);

    total += Number(pw) || 0;
    if (on) onCount += 1;
  });

  $('.sum-total', sec).textContent = fmtP(total);
  $('.oncount', sec).textContent = onCount;
  $('.ontotal', sec).textContent = ` / ${outs.length} 路`;

  const ci = $('.combo-info', sec);
  if (ci) {
    const m = comboMeta(d.id);
    if (!m) { ci.textContent = ''; }
    else {
      const [v, i] = cardPoint(d, logicalOutputs(d.id)[0]);
      ci.textContent = `${m.label} · ${Number(v).toFixed(2)}V / ${Number(i).toFixed(3)}A`;
    }
  }

  const idn = $('.dev-idn', sec);
  if (idn) {
    idn.textContent = d.connected ? (d.idn || '已连接') : (d.error ? '通信异常' : '未连接');
    idn.className = 'badge dev-idn' + (d.error ? ' warn' : (d.connected ? ' ok' : ''));
  }
}

function renderLoadPanel(st, d) {
  const sec = $('#view-' + d.id);
  if (!sec) return;
  const r = d.range || { max_v: 150, max_i: 30, max_p: 150, max_r: 7500 };
  $('.model-tag', sec).textContent = d.model;
  $('.card-name', sec).textContent = `电子负载 · ${d.model}`;
  $('.range-tag', sec).textContent = `量程 ${r.max_v}V / ${r.max_i}A / ${r.max_p}W`;

  const mode = S.loadMode[d.id] || 'CC';
  const meta = MODE_META[mode];
  const maxFor = { CC: r.max_i, CV: r.max_v, CR: r.max_r, CP: r.max_p }[mode];
  const valInp = $('.load-value', sec);
  valInp.max = maxFor;

  $('.rv', sec).textContent = Number(d.v).toFixed(3);
  $('.ri', sec).textContent = Number(d.i).toFixed(3);
  $('.rp', sec).textContent = Number(d.p).toFixed(3);

  const card = $('.load-ch', sec);
  card.classList.toggle('active', !!d.on);
  $('.state-tag', sec).textContent = d.connected ? `${d.mode} · ${Number(d.level).toFixed(3)} ${meta.unit}` : '—';

  const inp = $('[data-load-input]', sec);
  if (!inp.matches(':focus')) inp.checked = !!d.on;
  inp.disabled = !d.connected;

  if (d.mode && d.mode !== S.loadMode[d.id] && d.connected) {
    S.loadMode[d.id] = d.mode;
    syncLoadPanelMode(sec, d, false);
  }

  const idn = $('.dev-idn', sec);
  if (idn) {
    idn.textContent = d.connected ? (d.idn || '已连接') : (d.error ? '通信异常' : '未连接');
    idn.className = 'badge dev-idn' + (d.error ? ' warn' : (d.connected ? ' ok' : ''));
  }
}

function syncLoadPanelMode(sec, d, send) {
  const mode = S.loadMode[d.id] || 'CC';
  const meta = MODE_META[mode];
  const r = d.range || { max_v: 150, max_i: 30, max_p: 150, max_r: 7500 };
  const maxFor = { CC: r.max_i, CV: r.max_v, CR: r.max_r, CP: r.max_p }[mode];
  $('.value-label', sec).textContent = meta.label;
  $('.unit-tag', sec).textContent = meta.unit;
  const inp = $('.load-value', sec);
  inp.step = meta.step;
  inp.min = meta.min;
  inp.max = maxFor;
  $$('.mode-group button', sec).forEach(b => b.classList.toggle('sel', b.dataset.mode === mode));

  let v = parseFloat(inp.value);
  if (!isFinite(v) || v < meta.min || v > maxFor) { v = meta.def; inp.value = v; }
  if (send) {
    api('/api/load/set', { dev: d.id, mode, value: v }).catch(e => toast(e.message, true));
  }
}

/* ------------------------------------------------------------------ */
/* 渲染：曲线 / 数据表（按设备）                                       */
/* ------------------------------------------------------------------ */
function renderCurves(st) {
  const dev = curDev();
  if (!dev) return;
  if (dev.role === 'power') renderPowerCurves(st);
  else renderLoadCurves(st);
}

/* 原始数据表取样：最新在上，行间隔 >=0.75s，最多 n 行 */
function sampleRows(hist, n) {
  const rows = [];
  for (let i = hist.length - 1; i >= 0 && rows.length < n; i--) {
    const h = hist[i];
    if (rows.length && rows[rows.length - 1].t - h.t < 0.75) continue;
    rows.push(h);
  }
  return rows;
}

const pad2 = n => String(n).padStart(2, '0');
function fmtClock(t) {
  const d = new Date(t * 1000);
  return pad2(d.getHours()) + ':' + pad2(d.getMinutes()) + ':' + pad2(d.getSeconds());
}

/* 电压显示：正常 3 位小数，小值（关断噪声等）保留 3 位有效数字去尾零 */
function fmtV(x) {
  if (x == null || !isFinite(x)) return '—';
  const a = Math.abs(Number(x));
  if (a === 0 || a >= 0.01) return Number(x).toFixed(3);
  const d = Math.min(6, Math.max(3, 2 - Math.floor(Math.log10(a))));
  return Number(x).toFixed(d).replace(/(\.\d{3}\d*?)0+$/, '$1');
}
/* 电流/功率显示：小值保留 3 位有效数字（去尾零），精度不损失 */
function fmtI(x) {
  if (x == null || !isFinite(x)) return '—';
  const a = Math.abs(Number(x));
  if (a === 0) return '0.000';
  if (a >= 0.001) return Number(x).toFixed(3);
  const d = Math.min(6, Math.max(3, 2 - Math.floor(Math.log10(a))));
  return Number(x).toFixed(d).replace(/(\.\d{3}\d*?)0+$/, '$1');
}
function fmtP(x) {
  if (x == null || !isFinite(x)) return '—';
  const a = Math.abs(Number(x));
  if (a === 0) return '0.00';
  if (a >= 0.01) return Number(x).toFixed(2);
  const d = Math.min(6, Math.max(3, 2 - Math.floor(Math.log10(a))));
  return Number(x).toFixed(d).replace(/(\.\d{3}\d*?)0+$/, '$1');
}

function rowsHtml(rows, get, t0) {
  if (!rows.length) return '<tr><td colspan="4" class="td-empty">—</td></tr>';
  // 固定 6 位小数补零，列内位数一致；CSS 左对齐
  const f = x => (x == null ? '—' : Number(x).toFixed(6));
  return rows.map(h => {
    const a = get ? get(h) : null;
    return '<tr><td>' + fmtClock(h.t) + '</td>'
      + '<td class="col-v">' + f(a ? a[0] : null) + '</td>'
      + '<td class="col-i">' + f(a ? a[1] : null) + '</td>'
      + '<td class="col-p">' + f(a ? a[2] : null) + '</td></tr>';
  }).join('');
}

/* 横轴固定窗口：只取最近 secs 秒数据；secs=0 表示自动 */
function windowHist(hist, secs) {
  if (!secs || hist.length < 2) return hist;
  const cut = hist[hist.length - 1].t - secs;
  let start = 0;
  while (start < hist.length - 2 && hist[start].t < cut) start++;
  return hist.slice(start);
}

/* 电源曲线单元：按逻辑输出动态创建（组合模式下成员合并为一格） */
const powerCellStamps = {};
function ensurePowerCells(d) {
  const devStamp = S.combo[d.id] || 'none';
  if (powerCellStamps[d.id] === devStamp) return;
  powerCellStamps[d.id] = devStamp;
  const sec = $('#view-' + d.id);
  const row = $('.curve-row', sec), trow = $('.table-row', sec);
  row.innerHTML = '';
  trow.innerHTML = '';
  S.powerCells[d.id] = logicalOutputs(d.id).map((o, k) => {
    const cell = document.createElement('div');
    cell.className = 'curve-cell';
    cell.innerHTML = `<div class="cell-head"><b>${o.label}</b><span class="cell-state"></span></div>`
      + '<div class="cell-body"><canvas></canvas><div class="empty-tip">输出未开启</div></div>';
    const tcell = document.createElement('div');
    tcell.className = 'cell-table';
    tcell.innerHTML = '<table><thead><tr><th>时间 s</th><th>U/V</th><th>I/A</th><th>P/W</th></tr></thead>'
      + '<tbody><tr><td colspan="4" class="td-empty">—</td></tr></tbody></table>';
    row.appendChild(cell);
    trow.appendChild(tcell);
    const ref = {
      out: o,
      cv: cell.querySelector('canvas'),
      tip: cell.querySelector('.empty-tip'),
      st: cell.querySelector('.cell-state'),
      tbody: tcell.querySelector('tbody'),
    };
    bindCursor(ref.cv, 'power', d.id, k + 1);
    return ref;
  });
  S.cursor = { dev: '', kind: '', idx: -1, cell: 0 };
  S.curveStamp = '';
}

/* 电源：每台设备每逻辑输出一张横向小图 */
function renderPowerCurves(st) {
  const hist = st.history || [];
  const last = hist.length ? hist[hist.length - 1] : null;
  const span = last && hist.length > 1 ? Math.max(0, last.t - hist[0].t) : 0;
  const devs = st.devices.filter(x => x.role === 'power');

  const onMask = devs.map(d =>
    [1, 2, 3].map(c => (d.channels[c] && d.channels[c].on) ? 1 : 0).join('')).join('');
  const stamp = `power:${hist.length}:${last ? last.t : 0}:${onMask}:${S.axis.px}:${S.axis.py}:`
    + devs.map(d => S.combo[d.id] + ':' + (S.comboLive[d.id] || 'n')).join(',');
  if (stamp === S.curveStamp) return;
  S.curveStamp = stamp;

  const view = windowHist(hist, S.axis.px);
  const xs = view.map(h => h.t - view[0].t);

  devs.forEach(d => {
    ensurePowerCells(d);
    const sec = $('#view-' + d.id);
    const winEl = $('.curve-win', sec);
    if (winEl) {
      winEl.textContent = S.axis.px > 0
        ? `窗口 ${S.axis.px} s · 缓存 ${span.toFixed(0)} s`
        : `最近 ${span.toFixed(0)} s`;
    }
    const cells = S.powerCells[d.id] || [];
    const live = liveComboOf(d.id);
    cells.forEach((cd, k) => {
      const o = cd.out;
      const cv = cd.cv, tip = cd.tip, stEl = cd.st;
      const membersOn = o.chs.filter(c => d.channels[c] && d.channels[c].on);
      let engaged, occupied, on;
      if (o.combo) {
        engaged = S.comboLive[d.id] === o.mode;
        occupied = !engaged && membersOn.length > 0;
        on = engaged && membersOn.length === o.chs.length;
      } else {
        const c = o.chs[0];
        occupied = !!(live && live.chs.includes(c) && d.channels[c] && d.channels[c].on);
        on = !occupied && membersOn.length === 1;
      }
      if (stEl) {
        stEl.textContent = !d.connected ? '未连接'
          : occupied ? '成员占用中'
          : on ? '输出中' : '输出未开启';
        stEl.className = 'cell-state' + (on ? ' on' : (occupied ? ' occ' : ''));
      }
      if (cd.tbody) {
        // 表格用最近的原始点（recent），曲线才用降采样后的 history
        const recent = (st.recent && st.recent.length) ? st.recent : hist;
        cd.tbody.innerHTML = rowsHtml(sampleRows(recent, 200),
          h => combinedPoint(histPayload(h, d.id), o), recent.length ? recent[0].t : 0);
      }
      if (hist.length < 2) {
        const ctx = cv.getContext('2d');
        ctx.setTransform(1, 0, 0, 1, 0, 0);
        ctx.clearRect(0, 0, cv.width, cv.height);
        if (tip) {
          tip.style.display = 'flex';
          tip.textContent = !d.connected ? '未连接' : '正在采集…';
        }
        S.geom[`p:${d.id}:${k + 1}`] = null;
        return;
      }
      if (tip) tip.style.display = 'none';
      const g = plot(cv, {
        xs,
        ymax: S.axis.py,
        units: { l: 'V', r: 'A', r2: 'W' },
        panels: [{
          label: '',
          names: ['电压 V', '电流 A', '功率 W'],
          lc: '#4f9cf9', rc: '#2dd4bf', wc: '#f0a83c',
          series: [
            { vals: view.map(h => combinedPoint(histPayload(h, d.id), o)[0]), color: '#4f9cf9', width: 1.9 },
            { vals: view.map(h => combinedPoint(histPayload(h, d.id), o)[1]), color: '#2dd4bf', width: 1.7, axis: 'right' },
            { vals: view.map(h => combinedPoint(histPayload(h, d.id), o)[2]), color: '#f0a83c', axis: 'right2', width: 1.6 },
          ],
        }],
      });
      S.geom[`p:${d.id}:${k + 1}`] = g;
      if (!g || S.cursor.kind !== 'power' || S.cursor.dev !== d.id) return;
      drawCursor(g, S.cursor.idx, S.cursor.cell === k + 1);
    });
  });
}

/* 负载：每台设备一张图，同表 U/I/W */
function renderLoadCurves(st) {
  const hist = st.history || [];
  const last = hist.length ? hist[hist.length - 1] : null;
  const span = last && hist.length > 1 ? Math.max(0, last.t - hist[0].t) : 0;
  const devs = st.devices.filter(x => x.role === 'load');

  const onMask = devs.map(d => (d.on ? 1 : 0)).join('');
  const stamp = `load:${hist.length}:${last ? last.t : 0}:${onMask}:${S.axis.lx}:${S.axis.ly}`;
  if (stamp === S.curveStamp) return;
  S.curveStamp = stamp;

  const view = windowHist(hist, S.axis.lx);
  const xs = view.map(h => h.t - view[0].t);
  const pick = (arr, i) => (arr && arr[i] != null ? Number(arr[i]) : null);

  devs.forEach(d => {
    const sec = $('#view-' + d.id);
    if (!sec) return;
    const canvas = $('.curve-single canvas', sec);
    const tip = $('.curve-single .empty-tip', sec);
    const winEl = $('.curve-win', sec);
    const ltb = $('.load-table tbody', sec);
    const recent = (st.recent && st.recent.length) ? st.recent : hist;
    if (ltb) ltb.innerHTML = rowsHtml(sampleRows(recent, 200), h => histPayload(h, d.id).load, recent.length ? recent[0].t : 0);

    if (hist.length < 2) {
      const ctx = canvas.getContext('2d');
      ctx.setTransform(1, 0, 0, 1, 0, 0);
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      if (tip) {
        tip.style.display = 'flex';
        tip.textContent = d.connected ? '正在采集…' : '连接设备后开始采集';
      }
      if (winEl) winEl.textContent = '最近 0 s';
      S.geom[`l:${d.id}`] = null;
      return;
    }
    if (tip) tip.style.display = 'none';
    if (winEl) {
      winEl.textContent = S.axis.lx > 0
        ? `窗口 ${S.axis.lx} s · 缓存 ${span.toFixed(0)} s`
        : `最近 ${span.toFixed(0)} s`;
    }

    const g = plot(canvas, {
      xs,
      ymax: S.axis.ly,
      units: { l: 'V', r: 'A', r2: 'W' },
      panels: [{
        label: '',
        names: ['电压 V', '电流 A', '功率 W'],
        lc: '#4f9cf9', rc: '#2dd4bf', wc: '#f0a83c',
        series: [
          { vals: view.map(h => pick(histPayload(h, d.id).load, 0)), color: '#4f9cf9', width: 2 },
          { vals: view.map(h => pick(histPayload(h, d.id).load, 1)), color: '#2dd4bf', width: 1.8, axis: 'right' },
          { vals: view.map(h => pick(histPayload(h, d.id).load, 2)), color: '#f0a83c', axis: 'right2', width: 1.7 },
        ],
      }],
    });
    S.geom[`l:${d.id}`] = g;
    if (g && S.cursor.kind === 'load' && S.cursor.dev === d.id) drawCursor(g, S.cursor.idx, true);
  });
}

/* ------------------------------------------------------------------ */
/* 渲染：日志 / 扫描                                                   */
/* ------------------------------------------------------------------ */
function renderLogs(st) {
  const alerts = st.alerts || [];
  $('#log-badge').textContent = alerts.length || 0;
  $('#log-count').textContent = alerts.length ? `${alerts.length} 条` : '0 条';

  const newest = alerts.length ? alerts[0].t : 0;
  if (newest === S.lastAlertsTs) return;
  S.lastAlertsTs = newest;
  $('#logList').innerHTML = alerts.slice(0, 60).map(a => {
    const d = new Date(a.t * 1000);
    const hh = String(d.getHours()).padStart(2, '0');
    const mm = String(d.getMinutes()).padStart(2, '0');
    const ss = String(d.getSeconds()).padStart(2, '0');
    return `<div class="item ${a.level}"><span class="t">${hh}:${mm}:${ss}</span><span class="m">${escapeHtml(a.text)}</span></div>`;
  }).join('');
}

function fillDevSelect(sel, devs) {
  if (!sel) return;
  const keep = sel.value;
  sel.innerHTML = devs.length
    ? devs.map(d => `<option value="${d.id}">${escapeHtml(d.name)} · ${d.model}</option>`).join('')
    : '<option value="">（无可用设备）</option>';
  if (devs.some(d => d.id === keep)) sel.value = keep;
}

function renderSweep(st) {
  const sw = st.sweep;
  const pct = sw.total ? Math.round(sw.progress / sw.total * 100) : 0;
  $('#sweep-progress').style.width = pct + '%';
  $('#sweep-text').textContent = sw.running
    ? `${sw.progress} / ${sw.total} 点 (${pct}%) · ${sw.message || '扫描中'}`
    : (sw.points.length ? `${sw.points.length} 点 · ${sw.message || '就绪'}` : '—');

  $('#sweep-start').disabled = sw.running;
  $('#sweep-stop').disabled = !sw.running;
  $('#sweep-badge').textContent = sw.running ? '扫描中' : (sw.points.length ? '已完成' : '就绪');
  $('#sweep-badge').className = 'badge' + (sw.running ? ' warn' : (sw.points.length ? ' ok' : ''));
  $('#sweep-csv').disabled = !sw.points.length;

  fillDevSelect($('#sweep-pdev'), st.devices.filter(d => d.role === 'power'));
  fillDevSelect($('#sweep-ldev'), st.devices.filter(d => d.role === 'load'));

  // 扫描类型决定横轴含义
  const typeSel = $('#sweep-type');
  if (typeSel) {
    $('#sweep-src').textContent = typeSel.value === 'psu' ? '横轴：电源电压 V' : '横轴：负载电流 A';
  }

  const showP = !!S.showP.sweep;
  const tg = $('#sweep-ptoggle');
  if (tg) {
    tg.classList.toggle('on', showP);
    tg.setAttribute('aria-pressed', String(showP));
  }
  const lgI = $('#sweep-ilg'), lgP = $('#sweep-plg');
  if (lgI) lgI.classList.toggle('off', showP);
  if (lgP) lgP.classList.toggle('off', !showP);

  const key = `${sw.points.length}:${showP}`;
  if (key === S.lastSweepKey) return;
  S.lastSweepKey = key;
  if (!sw.points.length) {
    $('#sweep-tip').style.display = 'flex';
    return;
  }

  $('#sweep-tip').style.display = 'none';
  const right = showP
    ? { vals: sw.points.map(p => p.p), color: '#f0a83c', axis: 'right', width: 1.9, dash: [3, 3] }
    : { vals: sw.points.map(p => p.i), color: '#2dd4bf', axis: 'right', width: 2, dash: [7, 4] };
  plot($('#sweepCanvas'), {
    xs: sw.points.map(p => p.set),
    xUnit: typeSel && typeSel.value === 'psu' ? ' V' : ' A',
    panels: [{
      label: '',
      ru: showP ? 'W' : 'A',
      series: [
        { vals: sw.points.map(p => p.v), color: '#4f9cf9', axis: 'left', width: 2.2 },
        right,
      ],
    }],
  });
}

/* ------------------------------------------------------------------ */
let STATUS = null;

function render(st) {
  STATUS = st;
  S.ports = st.ports || [];
  ensurePanels(st);
  if (!S.booted) {
    S.booted = true;
    setView(st.devices.length ? st.devices[0].id : 'sweep');
  }
  renderNav(st);
  st.devices.forEach(d => {
    if (d.role === 'power') renderPowerPanel(st, d);
    else renderLoadPanel(st, d);
  });
  renderCurves(st);
  renderLogs(st);
  renderRecordings(st);
  renderSweep(st);
  if (S.view === 'sweep') renderLink(st);
}

async function tick() {
  try {
    render(await api('/api/status'));
  } catch (e) {
    console.warn(e);
  }
  setTimeout(tick, 700);
}

/* ------------------------------------------------------------------ */
/* 串口                                                               */
/* ------------------------------------------------------------------ */
function fillPortSelect() {
  const sel = $('#dev-port');
  const dev = curDev();
  const role = dev ? dev.role : 'power';
  const ports = S.ports || [];
  const cfg = dev ? (S.cfg[dev.id] || {}) : {};

  if (!ports.length) {
    sel.innerHTML = '<option value="">未发现串口</option>';
    return;
  }
  sel.innerHTML = ports.map(p =>
    `<option value="${p.device}">${p.device}${p.description ? ' — ' + p.description : ''}</option>`
  ).join('');

  const want = cfg.port;
  const exists = ports.some(p => p.device === want);
  if (exists) {
    sel.value = want;
  } else {
    // 未指定时：电源取第一个口，负载取第二个口（如果存在）
    const idx = role === 'load' && ports.length > 1 ? 1 : 0;
    sel.value = ports[idx].device;
    if (dev) S.cfg[dev.id].port = sel.value;
  }
}

// 波特率下拉按当前设备型号渲染 (型号支持的档位来自后端 model_specs;
// IT8510 老款仅 4800, IT8500+ 系列无 115200, IT6302 无 115200/19200)
function modelSpec(dev) {
  if (!dev) return null;
  return ((STATUS || {}).model_specs || {})[dev.role]?.[dev.model] || null;
}

function fillBaudSelect() {
  const sel = $('#dev-baud');
  const dev = curDev();
  const cfg = dev ? (S.cfg[dev.id] || {}) : {};
  const spec = modelSpec(dev);
  const rates = (spec && spec.baud_rates?.length) ? spec.baud_rates : [4800, 9600, 19200, 38400];
  const defBaud = spec?.baud_default || rates[0];
  sel.innerHTML = rates.map(b => `<option value="${b}">${b}</option>`).join('');
  // 上次记住的波特率仍在该型号支持列表内则沿用, 否则取型号默认
  const want = cfg.baud && rates.includes(cfg.baud) ? cfg.baud : defBaud;
  sel.value = String(want);
  if (dev) S.cfg[dev.id].baud = want;
}

async function refreshPorts(notify) {
  try {
    const r = await api('/api/ports');
    S.ports = r.ports || [];
    fillPortSelect();
    if (notify) toast(S.ports.length ? `发现 ${S.ports.length} 个串口` : '未发现串口', !S.ports.length);
  } catch (e) {
    if (notify) toast(e.message, true);
  }
}

/* ------------------------------------------------------------------ */
/* 导出                                                               */
/* ------------------------------------------------------------------ */
async function exportCsv(role) {
  const labels = { sweep: '扫描数据' };
  const bridge = window.pywebview && window.pywebview.api;

  if (bridge && bridge.save_csv) {
    try {
      const r = await bridge.save_csv(role);
      if (!r || r.cancelled) return;
      if (r.ok) toast(`${labels[role] || role}已保存\n${r.path}`);
      else toast(r.error || '保存失败', true);
    } catch (e) {
      toast('保存失败：' + e, true);
    }
    return;
  }
  window.location.href = `/api/export.csv?role=${role}`;
}

/* ------------------------------------------------------------------ */
/* 数据记录：开始记录 → 记录条目 → 结束记录时写出 CSV                   */
/* ------------------------------------------------------------------ */
let recCtx = null;      // {dev, role, devName}
let recMode = 'single'; // single=单路（仅单选） / multi=多路

function renderRecChans() {
  const chans = $('#rec-chans');
  if (!recCtx || recCtx.role !== 'power') {
    chans.innerHTML = '<div class="rec-role-tip">电子负载 · 电压 / 电流 / 功率（含工作模式）</div>';
    return;
  }
  // 按当前输出结构（独立三路 / 组合+独立）生成记录对象
  const outs = logicalOutputs(recCtx.dev);
  chans.innerHTML = outs.map((o, k) => {
    const type = recMode === 'single' ? 'radio' : 'checkbox';
    const checked = recMode === 'single' ? (k === 0 ? ' checked' : '') : ' checked';
    return `<label class="check"><input type="${type}" name="rec-ch" value="${k}"${checked}><span>${o.label}</span></label>`;
  }).join('');
}

function openRecDialog(devId) {
  const dev = STATUS ? STATUS.devices.find(d => d.id === devId) : null;
  if (!dev) return;
  recCtx = { dev: devId, role: dev.role, devName: dev.name };
  recMode = 'single';
  $$('#rec-mode-row .rec-mode').forEach(b => b.classList.toggle('sel', b.dataset.m === 'single'));
  $('#rec-mode-row').style.display = dev.role === 'power' ? '' : 'none';
  renderRecChans();
  const d = new Date();
  const pad = n => String(n).padStart(2, '0');
  const stamp = `${d.getFullYear()}${pad(d.getMonth() + 1)}${pad(d.getDate())}_${pad(d.getHours())}${pad(d.getMinutes())}${pad(d.getSeconds())}`;
  $('#rec-path').value = `${dev.name.replace(' ', '')}记录_${stamp}.csv`;
  let dir = '';
  try { dir = localStorage.getItem('itech.recdir') || ''; } catch (e) { /* 忽略 */ }
  $('#rec-dir').value = dir;
  const bridge = window.pywebview && window.pywebview.api;
  $('#rec-browse').style.display = bridge && bridge.pick_dir ? '' : 'none';
  try { $('#rec-iv').value = localStorage.getItem('itech.reciv') || '0.8'; } catch (e) { /* 忽略 */ }
  $('#rec-mask').style.display = 'flex';
  $('#rec-path').focus();
}

function closeRecDialog() {
  $('#rec-mask').style.display = 'none';
  recCtx = null;
}

async function confirmRec() {
  if (!recCtx) return;
  let outputs = null;
  if (recCtx.role === 'power') {
    const outs = logicalOutputs(recCtx.dev);
    const sel = $$('#rec-chans input:checked').map(c => parseInt(c.value, 10));
    if (!sel.length) { toast('请至少选择一个记录对象', true); return; }
    outputs = sel.map(k => ({
      chs: outs[k].chs,
      series: outs[k].combo ? outs[k].combo.series : false,
      label: outs[k].label,
    }));
  }
  const name = $('#rec-path').value.trim();
  if (!name) { toast('请填写保存文件名', true); return; }
  const dir = $('#rec-dir').value.trim();
  const path = dir ? dir.replace(/[\\/]+$/, '') + '/' + name : name;
  const interval = parseFloat($('#rec-iv').value) || 0.8;
  try {
    await api('/api/record/start', { dev: recCtx.dev, outputs, path, interval });
    try { localStorage.setItem('itech.recdir', dir); } catch (e) { /* 忽略 */ }
    try { localStorage.setItem('itech.reciv', $('#rec-iv').value); } catch (e) { /* 忽略 */ }
    closeRecDialog();
    toast('记录已开始，点击「结束记录」导出 CSV');
  } catch (e) { toast(e.message, true); }
}

async function stopRecording(id, btn) {
  btn.disabled = true;
  btn.textContent = '导出中…';
  try {
    const r = await api('/api/record/stop', { id });
    toast(`记录已导出（${r.count} 点）\n${r.path}`);
    const el = btn.closest('.rec-item');
    if (el) el.remove();
  } catch (e) {
    toast(e.message, true);
    btn.disabled = false;
    btn.textContent = '结束记录';
  }
}

function renderRecordings(st) {
  const recs = st.recordings || [];
  st.devices.forEach(d => {
    const sec = $('#view-' + d.id);
    if (!sec) return;
    const bar = $('.rec-bar', sec);
    const list = recs.filter(r => r.dev === d.id);
    // 负载只有一路：记录中直接隐藏「开始记录」按钮，防止重复开启
    if (d.role === 'load') {
      const btn = $('[data-act="rec"]', sec);
      if (btn) btn.style.display = list.length ? 'none' : '';
    }
    // 服务端已结束的条目自动移除
    bar.querySelectorAll('.rec-item').forEach(el => {
      if (!list.some(r => String(r.id) === el.dataset.id)) el.remove();
    });
    if (!list.length) { bar.style.display = 'none'; return; }
    bar.style.display = 'flex';
    list.forEach(r => {
      let el = bar.querySelector(`.rec-item[data-id="${r.id}"]`);
      if (!el) {
        el = document.createElement('div');
        el.className = 'rec-item';
        el.dataset.id = String(r.id);
        const what = r.role === 'power' && r.outputs && r.outputs.length
          ? r.outputs.map(o => o.label).join(' + ')
          : escapeHtml(d.name);
        const t0 = new Date(r.t0 * 1000);
        const pad = n => String(n).padStart(2, '0');
        el.innerHTML =
          `<i class="rec-dot"></i>` +
          `<span class="rec-txt">记录中 · ${what} · ${Math.round((r.interval || 0.8) * 10) / 10}s/点 · 自 ${pad(t0.getHours())}:${pad(t0.getMinutes())}:${pad(t0.getSeconds())} · <b class="rec-count">0</b> 点</span>` +
          `<button class="tiny ghost rec-stop">结束记录</button>`;
        el.querySelector('.rec-stop').onclick = ev => stopRecording(r.id, ev.target);
        bar.appendChild(el);
      }
      const cnt = el.querySelector('.rec-count');
      if (cnt) cnt.textContent = r.count;
    });
  });
}

/* ------------------------------------------------------------------ */
/* 事件绑定（面板动态生成，事件委托到 document）                        */
/* ------------------------------------------------------------------ */
function bind() {
  // 主题切换
  $('#theme-toggle').onclick = () => {
    THEME = THEME === 'light' ? 'dark' : 'light';
    try { localStorage.setItem('itech.theme', THEME); } catch (e) { /* 忽略 */ }
    applyTheme(THEME);
    S.curveStamp = '';                 // 画布配色随主题重绘
    if (STATUS) renderCurves(STATUS);
  };

  // 接线指引对话框：确认已接线后执行挂起的"开启组合输出"
  let pendingWireOn = null;
  $('#wire-cancel').onclick = () => {
    $('#wire-mask').style.display = 'none';
    pendingWireOn = null;
    toast('已取消：输出状态未改变');
  };
  $('#wire-ok').onclick = async () => {    $('#wire-mask').style.display = 'none';
    const p = pendingWireOn; pendingWireOn = null;
    if (!p) return;
    try {
      // 成员通道原有输出先关闭，再开启组合输出
      for (const c of p.chs) {
        try { await api('/api/power/output', { dev: p.devId, ch: c, on: false }); } catch (e) { /* 忽略 */ }
      }
      for (const c of p.chs) await api('/api/power/output', { dev: p.devId, ch: c, on: true });
      S.comboLive[p.devId] = p.mode;   // 标记：这些通道现已按该组合接通
      saveComboState();
      toast(`${p.label} 输出已开启`);
    } catch (e) { toast(e.message, true); }
  };

  // 组合占用弹窗：关闭组合输出（保留接线），之后可按单路方式开启
  $('#combooff-cancel').onclick = () => {
    $('#combooff-mask').style.display = 'none';
    comboOffCtx = null;
  };
  $('#combooff-ok').onclick = async () => {
    const p = comboOffCtx; comboOffCtx = null;
    $('#combooff-mask').style.display = 'none';
    if (!p) return;
    const members = COMBO[p.mode] ? COMBO[p.mode].chs.slice() : [];
    const st = STATUS && STATUS.devices.find(d => d.id === p.devId);
    try {
      // 1) 关闭组合输出的全部成员通道
      for (const c of members) {
        try { await api('/api/power/output', { dev: p.devId, ch: c, on: false }); } catch (e) { /* 忽略 */ }
      }
      S.comboLive[p.devId] = null;
      saveComboState();
      // 本地同步关闭状态，避免下一次渲染仍用旧 STATUS 把开关闪成 ON
      if (st) members.forEach(c => { if (st.channels[c]) st.channels[c].on = false; });
      // 2) 继续完成用户原本的操作：按单路开启其请求的通道
      let onMsg = '';
      if (p.chs && p.chs.length) {
        for (const c of p.chs) await api('/api/power/output', { dev: p.devId, ch: c, on: true });
        if (st) p.chs.forEach(c => { if (st.channels[c]) st.channels[c].on = true; });
        onMsg = `，${p.chs.map(c => 'CH' + c).join('、')} 已按单路开启`;
      }
      S.curveStamp = '';
      if (STATUS) { renderPowerPanel(STATUS, STATUS.devices.find(d => d.id === p.devId)); renderCurves(STATUS); }
      toast(`已退出组合输出${onMsg}`);
    } catch (e) { toast(e.message, true); }
  };

  // 静态导航：联动测试
  const sweepNav = $('.dev[data-role="sweep"]');
  if (sweepNav) sweepNav.onclick = () => setView('sweep');

  // 添加仪器对话框
  $('#add-inst').onclick = () => {
    if (!STATUS) return;
    const typeSel = $('#add-type'), modelSel = $('#add-model');
    const fillModels = () => {
      const list = (STATUS.models || {})[typeSel.value] || [];
      modelSel.innerHTML = list.map(m => `<option>${m}</option>`).join('');
    };
    typeSel.onchange = fillModels;
    fillModels();
    $('#add-mask').style.display = 'flex';
  };
  $('#add-cancel').onclick = () => { $('#add-mask').style.display = 'none'; };
  $('#add-ok').onclick = async () => {
    const role = $('#add-type').value, model = $('#add-model').value;
    try {
      await api('/api/instrument/add', { role, model });
      $('#add-mask').style.display = 'none';
      toast(`已添加 ${model}，请在连接设置中选择串口连接`);
    } catch (e) { toast(e.message, true); }
  };

  // 点击委托：面板动作 / 组合按钮 / 常用值 / 负载模式
  document.addEventListener('click', ev => {
    // 接线指引警示条内：手动关闭正在输出的成员通道
    const woff = ev.target.closest('[data-wire-off]');
    if (woff && wireCtx) {
      const ch = Number(woff.dataset.wireOff);
      woff.disabled = true;
      api('/api/power/output', { dev: wireCtx.devId, ch, on: false })
        .then(() => {
          toast(`CH${ch} 输出已关闭`);
          const st = STATUS && STATUS.devices.find(d => d.id === wireCtx.devId);
          if (st && st.channels[ch]) st.channels[ch].on = false;   // 本地同步，免等下一轮轮询
          const warn = $('#wire-body .wire-warn');
          if (warn) warn.outerHTML = wireWarnHtml(wireCtx.mode, wireCtx.devId);
        })
        .catch(e => { woff.disabled = false; toast(e.message, true); });
      return;
    }

    const actEl = ev.target.closest('[data-act]');
    if (actEl) {
      const sec = actEl.closest('.view');
      const devId = sec ? sec.dataset.view : null;
      const act = actEl.dataset.act;
      if (act === 'all-on' && devId) {
        api('/api/power/output', { dev: devId, ch: null, on: true })
          .then(() => {
            if (comboMeta(devId)) { S.comboLive[devId] = S.combo[devId]; saveComboState(); }   // 全部 ON 视为按当前结构接通
            toast('三路输出已开启');
          }).catch(e => toast(e.message, true));
      } else if (act === 'all-off' && devId) {
        api('/api/power/output', { dev: devId, ch: null, on: false })
          .then(() => { S.comboLive[devId] = null; saveComboState(); toast('三路输出已关闭'); }).catch(e => toast(e.message, true));
      } else if (act === 'rec' && devId) {
        openRecDialog(devId);
      } else if (act === 'clear-curves') {
        api('/api/history/clear', {})
          .then(() => { S.curveStamp = ''; toast('曲线已清空'); })
          .catch(e => toast(e.message, true));
      }
      return;
    }

    const seg = ev.target.closest('.seg-btn');
    if (seg) {
      const sec = seg.closest('.view');
      const devId = sec.dataset.view;
      const wantCombo = seg.dataset.c === 'combo';
      if (wantCombo === (S.combo[devId] !== 'none')) return;
      const prev = S.combo[devId] || 'none';
      S.combo[devId] = wantCombo ? ($('[data-combo-mode]', sec).value || 's12') : 'none';
      applyCombo(devId, prev, wantCombo);
      return;
    }

    const preset = ev.target.closest('.v-presets button');
    if (preset) {
      const inp = preset.closest('.v-field').querySelector('input');
      inp.value = Number(preset.dataset.v).toFixed(2);
      inp.dispatchEvent(new Event('change', { bubbles: true }));
      return;
    }

    const modeBtn = ev.target.closest('.mode-group button');
    if (modeBtn) {
      const sec = modeBtn.closest('.view');
      const dev = STATUS && STATUS.devices.find(x => x.id === sec.dataset.view);
      if (!dev) return;
      const next = modeBtn.dataset.mode;
      if (next === S.loadMode[dev.id]) return;
      S.loadMode[dev.id] = next;
      $('.load-value', sec).value = MODE_META[next].def;
      syncLoadPanelMode(sec, dev, true);
      return;
    }

    const del = ev.target.closest('.dev-del');
    if (del) {
      // 侧栏重建后由 bindDelBadge 接管；此处兜底
      return;
    }
  });

  // 组合输出切换：不改变电源当前输出状态
  const applyCombo = async (devId, prevCombo, wantCombo) => {
    const segBtns = $$(`#view-${devId} .seg-btn`);
    const comboSel = $(`#view-${devId} [data-combo-mode]`);
    const combo = S.combo[devId] !== 'none';
    segBtns.forEach(b => b.classList.toggle('sel', b.dataset.c === (combo ? 'combo' : 'none')));
    comboSel.style.display = combo ? '' : 'none';
    if (combo) comboSel.value = S.combo[devId];

    // 切换单路/组合仅改变视图与结构选择，不改变电源输出状态，也不清除组合接通标记
    // （comboLive 保留：单路视图下被组合接通的成员通道会显示"占用"）
    try {
      saveComboState();
    } catch (e) { /* 忽略 */ }
    if (wantCombo) {
      const m = COMBO[S.combo[devId]];
      toast(`已切换到${m.label}（输出状态未改变）。开启组合输出时会先提示接线方式`);
    } else {
      toast('已切换到单路输出（输出状态未改变）。如之前接了组合跳线，请拆除后再单路使用');
    }
    S.curveStamp = '';
    if (STATUS) { renderPowerPanel(STATUS, STATUS.devices.find(d => d.id === devId)); renderCurves(STATUS); }
  };

  // change 委托：电源卡 / 组合下拉 / 负载 / 轴选择
  document.addEventListener('change', async ev => {
    const t = ev.target;

    if (t.matches('[data-combo-mode]')) {
      const sec = t.closest('.view');
      const devId = sec.dataset.view;
      if (t.value === S.combo[devId]) return;
      const prev = S.combo[devId];
      S.combo[devId] = t.value;
      applyCombo(devId, prev, true);
      return;
    }

    const card = t.closest('.ch');
    if (card && (t.matches('[data-out]') || t.matches('[data-set]'))) {
      const sec = card.closest('.view');
      const devId = sec.dataset.view;
      const chs = card.dataset.chs.split(',').map(Number);
      // 只看这张卡本身是不是组合卡（.combo），不能按视图判断——
      // 组合视图里的 CH3 等非成员卡仍是单路卡，走错分支会误弹接线指引/误分解设定
      const m = card.classList.contains('combo') ? comboMeta(devId) : null;

      if (t.matches('[data-out]')) {
        const on = t.checked;
        const live = liveComboOf(devId);
        // 组合卡开启输出：先弹接线指引，确认已接线后才真正开启
        if (on && m) {
          t.checked = false;
          pendingWireOn = { devId, chs, mode: S.combo[devId], label: card.querySelector('.ch-name').textContent };
          showWireGuide(S.combo[devId], devId);
          return;
        }
        // 单路卡开启输出：该通道正作为已接通组合的成员被占用，需先关闭组合输出
        if (on && !m && live && chs.some(c => live.chs.includes(c))) {
          t.checked = false;
          showComboOffDialog(devId, live, chs);
          return;
        }
        try {
          for (const c of chs) await api('/api/power/output', { dev: devId, ch: c, on });
          // 成员通道被关闭 → 组合占用解除（组合卡 OFF 或单路下关掉成员）
          if (!on && (m || (live && chs.some(c => live.chs.includes(c))))) { S.comboLive[devId] = null; saveComboState(); }
        } catch (e) { t.checked = !on; toast(e.message, true); }
        return;
      }

      if (t.matches('[data-set]')) {
        const v = parseFloat(card.querySelector('[data-set="v"]').value);
        const i = parseFloat(card.querySelector('[data-set="i"]').value);
        // 输出开启时修改设定：先把输出关闭（防止带载改压/误触直接影响输出），
        // 用户确认设定无误后手动重新开启
        const live = liveComboOf(devId);
        const devNow = STATUS && STATUS.devices.find(x => x.id === devId);
        const wasOn = chs.filter(c => devNow && devNow.channels[c] && devNow.channels[c].on);
        let offMsg = '';
        if (wasOn.length) {
          try {
            for (const c of wasOn) await api('/api/power/output', { dev: devId, ch: c, on: false });
            if (m || (live && chs.some(c => live.chs.includes(c)))) { S.comboLive[devId] = null; saveComboState(); }
            if (devNow) wasOn.forEach(c => { if (devNow.channels[c]) devNow.channels[c].on = false; });
            offMsg = '；输出已关闭，请确认后重新开启';
          } catch (e) { toast(e.message, true); return; }
        }
        // 组合设定分解到成员通道
        let plan = [];
        if (!m) {
          plan = chs.map(c => ({ ch: c, v: isFinite(v) ? v : null, i: isFinite(i) ? i : null }));
        } else if (m.series) {
          let v1 = null, v2 = null;
          if (isFinite(v)) {
            if (S.combo[devId] === 's12') { v1 = v / 2; v2 = v / 2; }
            else {                   // s13/s23：30V 通道吃满，余量给 5V 通道
              v1 = Math.min(30, v); v2 = v - v1;
              if (v2 > 5 + 1e-9) { toast('总电压超出 35V 上限', true); return; }
            }
          }
          plan = m.chs.map((c, idx) => ({ ch: c, v: idx === 0 ? v1 : v2, i: isFinite(i) ? i : null }));
        } else {                     // 并联：电压相同，电流对半
          plan = m.chs.map(c => ({ ch: c, v: isFinite(v) ? v : null, i: isFinite(i) ? i / 2 : null }));
        }
        try {
          for (const a of plan) {
            const body = { dev: devId, ch: a.ch };
            if (a.v != null) body.v = a.v;
            if (a.i != null) body.i = a.i;
            await api('/api/power/set', body);
          }
          toast(`${card.querySelector('.ch-name').textContent} 已设定 `
            + `${isFinite(v) ? v.toFixed(2) + 'V' : '—'} / ${isFinite(i) ? i.toFixed(3) + 'A' : '—'}${offMsg}`);
        } catch (e) { toast(e.message, true); }
        return;
      }
    }

    if (t.matches('[data-load-input]')) {
      const sec = t.closest('.view');
      const devId = sec.dataset.view;
      try { await api('/api/load/input', { dev: devId, on: t.checked }); }
      catch (e) { t.checked = !t.checked; toast(e.message, true); }
      return;
    }

    if (t.matches('.load-value')) {
      const sec = t.closest('.view');
      const dev = STATUS && STATUS.devices.find(x => x.id === sec.dataset.view);
      if (!dev) return;
      const v = parseFloat(t.value);
      if (!isFinite(v)) return toast('请输入有效数值', true);
      const mode = S.loadMode[dev.id] || 'CC';
      try { await api('/api/load/set', { dev: dev.id, mode, value: v }); toast(`${dev.name} ${mode} = ${v}`); }
      catch (e) { toast(e.message, true); }
      return;
    }

    if (t.matches('[data-axis]')) {
      S.axis[t.dataset.axis] = parseFloat(t.value) || 0;
      S.curveStamp = '';
      if (STATUS) renderCurves(STATUS);
      return;
    }
  });

  // 表格高度拖动（面板动态生成，委托 mousedown）
  document.addEventListener('mousedown', ev => {
    const hd = ev.target.closest('.cell-drag');
    if (!hd) return;
    ev.preventDefault();
    const curves = hd.closest('.curves');
    const tables = hd.nextElementSibling && hd.nextElementSibling.classList.contains('cell-table')
      ? [hd.nextElementSibling]
      : $$('.table-row .cell-table', curves);
    if (!tables.length) return;
    const startH = tables[0].getBoundingClientRect().height;
    const startY = ev.clientY;
    hd.classList.add('on');
    document.body.style.cursor = 'row-resize';
    document.body.style.userSelect = 'none';
    const onMove = e => {
      const h = Math.round(Math.min(560, Math.max(70, startH - (e.clientY - startY))));
      if (tables[0].style.height !== h + 'px') {
        tables.forEach(tb => { tb.style.height = h + 'px'; });
        S.curveStamp = '';
        if (STATUS) renderCurves(STATUS);
      }
    };
    const onUp = () => {
      hd.classList.remove('on');
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
      window.removeEventListener('mousemove', onMove);
      window.removeEventListener('mouseup', onUp);
    };
    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
  });

  window.__reflow = () => { S.curveStamp = ''; if (STATUS) renderCurves(STATUS); };

  // 侧栏连接设置（作用于当前选中的设备）
  $('#dev-port').onchange = ev => {
    const dev = curDev();
    if (dev) S.cfg[dev.id].port = ev.target.value;
  };
  $('#dev-baud').onchange = ev => {
    const dev = curDev();
    if (dev) S.cfg[dev.id].baud = parseInt(ev.target.value, 10);
  };
  $('#dev-model').onchange = () => {
    // 换型号: 波特率档位跟着型号变, 取该型号默认值
    const dev = curDev();
    if (!dev) return;
    dev.model = $('#dev-model').value;
    fillBaudSelect();
  };

  $('#dev-connect').onclick = async () => {
    const dev = curDev();
    if (!dev) return;
    const cfg = S.cfg[dev.id] || (S.cfg[dev.id] = { port: '', baud: 0 });
    const port = $('#dev-port').value || cfg.port;
    const baud = parseInt($('#dev-baud').value, 10) || 0;
    if (!port && !$('#sim-mode').checked) return toast('请选择串口，或勾选模拟模式', true);
    try {
      await api('/api/connect', {
        dev: dev.id, port, baud,
        sim: $('#sim-mode').checked,
        safe_start: $('#safe-start').checked,
        model: dev.role === 'load' ? $('#dev-model').value : undefined,
      });
      toast(`${dev.name} 已连接`);
    } catch (e) { toast(e.message, true); }
  };

  $('#dev-disconnect').onclick = async () => {
    const dev = curDev();
    if (!dev) return;
    try {
      await api('/api/disconnect', { dev: dev.id });
      toast('已断开');
    } catch (e) { toast(e.message, true); }
  };

  $('#refresh-ports').onclick = () => refreshPorts(true);

  $('#selftest').onclick = async () => {
    const dev = curDev();
    if (!dev || !STATUS) return;
    if (!dev.connected) return toast('请先连接该设备', true);
    try {
      const r = await api('/api/selftest', { dev: dev.id });
      toast(r.ok ? '通信自检通过，详见运行日志' : '自检存在异常，详见运行日志', !r.ok);
    } catch (e) { toast(e.message, true); }
  };

  // 急停
  $('#estop').onclick = async () => {
    const btn = $('#estop');
    // 本地立即反馈：不等轮询，直接把界面上所有输出开关置 OFF
    $$('[data-out]').forEach(c => { c.checked = false; });
    $$('[data-load-input]').forEach(c => { c.checked = false; });
    Object.keys(S.comboLive).forEach(k => { S.comboLive[k] = null; });   // 全部关断后组合占用解除
    saveComboState();
    btn.classList.add('fired');
    btn.textContent = '已全部关断';
    setTimeout(() => { btn.classList.remove('fired'); btn.textContent = '急停 · 全部关断'; }, 2600);
    try { await api('/api/estop', {}); toast('已执行急停：所有输出关闭'); }
    catch (e) { toast(e.message, true); }
  };

  // 日志抽屉
  $('#log-open').onclick = toggleLog;
  $('#log-close').onclick = closeLog;

  document.addEventListener('keydown', ev => {
    if (ev.key === 'Escape' && document.body.classList.contains('log-open')) closeLog();
  });

  // 扫描右轴切换
  const ptoggle = $('#sweep-ptoggle');
  if (ptoggle) {
    ptoggle.onclick = () => {
      S.showP.sweep = !S.showP.sweep;
      S.lastSweepKey = '';
      if (STATUS && S.view === 'sweep') renderSweep(STATUS);
    };
  }

  $('#sweep-type').onchange = ev => {
    const t = ev.target.value;
    $$('[data-grp]').forEach(el => { el.style.display = el.dataset.grp === t ? '' : 'none'; });
    const src = $('#sweep-src');
    if (src) src.textContent = t === 'psu' ? '横轴：电源电压 V' : '横轴：负载电流 A';
  };

  $('#sweep-start').onclick = async () => {
    if (!STATUS) return;
    const pdev = STATUS.devices.find(d => d.id === $('#sweep-pdev').value);
    const ldev = STATUS.devices.find(d => d.id === $('#sweep-ldev').value);
    if (!pdev || !ldev || !pdev.connected || !ldev.connected) {
      return toast('扫描需要所选电源与负载同时连接', true);
    }
    const type = $('#sweep-type').value;
    const body = {
      type,
      pdev: pdev.id,
      ldev: ldev.id,
      ch: parseInt($('#sweep-ch').value, 10),
      dwell_ms: parseFloat($('[data-name="dwell_ms"]').value) || 300,
      safe_end: $('#sweep-safe').checked,
    };
    $$(`[data-grp="${type}"] [data-name]`).forEach(el => {
      body[el.dataset.name] = parseFloat(el.value);
    });
    try { await api('/api/sweep/start', body); toast('扫描已开始'); }
    catch (e) { toast(e.message, true); }
  };

  $('#sweep-stop').onclick = async () => {
    try { await api('/api/sweep/stop'); toast('正在停止…'); }
    catch (e) { toast(e.message, true); }
  };

  // 记录 / 导出
  $('#sweep-csv').onclick = () => exportCsv('sweep');

  // 记录对话框
  $$('#rec-mode-row .rec-mode').forEach(b => {
    b.onclick = () => {
      recMode = b.dataset.m;
      $$('#rec-mode-row .rec-mode').forEach(x => x.classList.toggle('sel', x === b));
      $('#rec-mode-tip').textContent = recMode === 'single' ? '单路：只记录一个对象' : '多路：可同时勾选多个对象';
      renderRecChans();
    };
  });
  $('#rec-cancel').onclick = closeRecDialog;
  $('#rec-ok').onclick = confirmRec;
  $('#rec-browse').onclick = async () => {
    const bridge = window.pywebview && window.pywebview.api;
    if (!bridge || !bridge.pick_dir) { toast('请在桌面版中使用系统目录选择', true); return; }
    try {
      const r = await bridge.pick_dir();
      if (r && r.ok && r.path) $('#rec-dir').value = r.path;
    } catch (e) { toast(e.message, true); }
  };

  // 左右栏宽度拖动
  const split = $('#col-split');
  if (split) {
    split.addEventListener('mousedown', ev => {
      ev.preventDefault();
      const shell = $('.shell');
      const side = $('.sidebar');
      const startX = ev.clientX;
      const startW = side.getBoundingClientRect().width;
      split.classList.add('on');
      document.body.style.cursor = 'col-resize';
      document.body.style.userSelect = 'none';
      let lastPaint = 0;
      const onMove = e => {
        const w = Math.round(Math.min(460, Math.max(230, startW + (e.clientX - startX))));
        shell.style.gridTemplateColumns = `${w}px 6px minmax(0, 1fr)`;
        const now = performance.now();
        if (now - lastPaint > 120) {          // 拖动中节流重绘
          lastPaint = now;
          if (STATUS) { S.curveStamp = ''; S.lastSweepKey = ''; renderCurves(STATUS); renderSweep(STATUS); }
        }
      };
      const onUp = () => {
        split.classList.remove('on');
        document.body.style.cursor = '';
        document.body.style.userSelect = '';
        window.removeEventListener('mousemove', onMove);
        window.removeEventListener('mouseup', onUp);
        if (STATUS) { S.curveStamp = ''; S.lastSweepKey = ''; renderCurves(STATUS); renderSweep(STATUS); }
      };
      window.addEventListener('mousemove', onMove);
      window.addEventListener('mouseup', onUp);
    });
  }

  let rzTimer = null;
  window.addEventListener('resize', () => {
    clearTimeout(rzTimer);
    rzTimer = setTimeout(() => {
      if (!STATUS) return;
      S.curveStamp = '';
      S.lastSweepKey = '';
      renderCurves(STATUS);
      renderSweep(STATUS);
    }, 120);
  });
}

function bindCursor(cv, kind, devId, cell) {
  if (!cv) return;
  const geomKey = kind === 'power' ? `p:${devId}:${cell}` : `l:${devId}`;
  const clear = () => {
    if (S.cursor.dev !== devId || S.cursor.kind !== kind) return;
    S.cursor = { dev: '', kind: '', idx: -1, cell: 0 };
    S.curveStamp = '';
    if (STATUS) renderCurves(STATUS);
  };
  cv.addEventListener('mousemove', ev => {
    const g = S.geom[geomKey];
    if (!g) return;
    const r = cv.getBoundingClientRect();
    const px = ev.clientX - r.left;
    if (px < g.padL - 6 || px > g.padL + g.pw + 6) { clear(); return; }
    const xv = g.xmin + (px - g.padL) / g.pw * (g.xmax - g.xmin);
    let lo = 0, hi = g.xs.length - 1;
    while (hi - lo > 1) {
      const mid = (lo + hi) >> 1;
      if (g.xs[mid] < xv) lo = mid; else hi = mid;
    }
    const idx = Math.abs(g.xs[lo] - xv) <= Math.abs(g.xs[hi] - xv) ? lo : hi;
    if (S.cursor.kind === kind && S.cursor.dev === devId && S.cursor.idx === idx && S.cursor.cell === cell) return;
    S.cursor = { dev: devId, kind, idx, cell };
    S.curveStamp = '';
    if (STATUS) renderCurves(STATUS);
  });
  cv.addEventListener('mouseleave', clear);
}

/* 深链接：#sweep / #log；旧 #power/#load 落到对应角色第一台 */
function applyHash() {
  const h = (location.hash || '').replace('#', '');
  if (h === 'sweep') setView('sweep');
  else if (h === 'log') openLog();
  else if (h === 'power' || h === 'load') {
    const d = STATUS && STATUS.devices.find(x => x.role === h);
    if (d) setView(d.id);
  }
}

window.addEventListener('pywebviewready', () => {
  S.desktop = true;
  document.body.classList.add('desktop');
});

window.addEventListener('hashchange', applyHash);

function openLog() { document.body.classList.add('log-open'); }
function closeLog() { document.body.classList.remove('log-open'); }
function toggleLog() {
  if (document.body.classList.contains('log-open')) closeLog(); else openLog();
}

/* ---------------- 主题：深色 / 浅色 ---------------- */
function applyTheme(t) {
  document.documentElement.classList.toggle('light', t === 'light');
  const b = $('#theme-toggle');
  if (b) b.textContent = t === 'light' ? '🌙 深色' : '☀️ 浅色';
}
let THEME = 'dark';
try { THEME = localStorage.getItem('itech.theme') === 'light' ? 'light' : 'dark'; } catch (e) { /* 忽略 */ }
applyTheme(THEME);

bind();
tick();
applyHash();



