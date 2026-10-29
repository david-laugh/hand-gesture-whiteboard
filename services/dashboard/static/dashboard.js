const $ = id => document.getElementById(id);
const SVG = 'http://www.w3.org/2000/svg';

const STAGES = [
  {key: 'uplink', name: '휴대폰 → gateway', desc: 'gateway 수신 − 촬영 시각'},
  {key: 'gateway', name: 'gateway', desc: '대기열 + frames 발행'},
  {key: 'vision', name: 'vision', desc: 'frames → landmarks'},
  {key: 'gesture', name: 'gesture', desc: 'landmarks → gestures'},
  {key: 'canvas', name: 'canvas', desc: 'gestures → canvas'},
];
const OUTCOME_LABEL = {complete: '완료', vision_dropped: 'vision 드롭', no_gesture: 'gesture 없음', no_canvas: 'canvas 없음'};
const VISION_ROWS = [
  ['queue_wait_ms', '대기 (수신 → 처리 시작)'], ['decode_ms', 'JPEG 디코딩'], ['inference_ms', 'MediaPipe 추론'],
  ['serialization_ms', '직렬화'], ['publish_ms', 'MQTT 발행'], ['processing_ms', '처리 합계 (디코딩 + 추론 + 직렬화)'],
];

const ms = v => (v === null || v === undefined ? '–' : `${Math.round(v)} ms`);
const msPrecise = v => (v === null || v === undefined ? '–' : `${v < 10 ? v.toFixed(1) : Math.round(v)} ms`);
const pct = v => (v === null || v === undefined ? '–' : `${(v * 100).toFixed(1)}%`);
const el = (tag, attrs = {}, text) => {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
  if (text !== undefined) node.textContent = text;
  return node;
};
const svg = (tag, attrs = {}) => {
  const node = document.createElementNS(SVG, tag);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
  return node;
};

// -- tooltip ------------------------------------------------------------------------------------
const tip = $('tooltip');
function showTip(html, x, y) {
  tip.innerHTML = html;
  tip.hidden = false;
  const r = tip.getBoundingClientRect();
  tip.style.left = `${Math.min(x + 14, window.innerWidth - r.width - 8)}px`;
  tip.style.top = `${Math.max(8, y - r.height - 12)}px`;
}
const hideTip = () => { tip.hidden = true; };

// -- sections -----------------------------------------------------------------------------------
function renderStatus(s) {
  const box = $('status');
  box.replaceChildren();
  const add = (text, cls) => box.append(el('span', cls ? {class: cls} : {}, text));
  add(`MQTT ${s.mqtt.connected ? '연결됨' : '끊김'} (${s.mqtt.broker}, ${s.mqtt.prefix}/…)`, s.mqtt.connected ? 'ok' : 'bad');
  if (s.current_session) {
    const idle = s.current_session_idle_ms;
    add(`현재 촬영 세션 ${s.current_session.slice(0, 8)}… · ${idle < 2000 ? '수신 중' : `${Math.round(idle / 1000)}초 전 마지막 프레임`}`);
  } else add('아직 촬영 세션 없음');
  add(`집계 중 프레임 ${s.open_frames}`);
  if (s.vision) add(`vision /metrics ${s.vision.ok ? '수신' : '실패'}`, s.vision.ok ? 'ok' : 'bad');
}

function renderTiles(s) {
  const e2e = s.stages.e2e;
  $('t-e2e50').textContent = ms(e2e.p50);
  $('t-e2e95').textContent = ms(e2e.p95);
  $('t-fps').textContent = s.fps.canvas ? `${s.fps.canvas}` : '0';
  $('t-fps-note').textContent = `카메라 입력 ${s.fps.frames} FPS`;
  $('t-complete').textContent = pct(s.rates.complete);
  $('t-complete-note').textContent = `최근 ${s.window_s}초 · ${s.frames_in_window}프레임`;
}

function renderStack(s) {
  const box = $('stack');
  box.replaceChildren();
  const vals = STAGES.map(st => ({...st, v: s.stages[st.key].p50, p95: s.stages[st.key].p95, n: s.stages[st.key].n}));
  const total = vals.reduce((a, b) => a + Math.max(0, b.v ?? 0), 0);
  $('stack-caption').textContent = total ? `(각 단계 p50 · 합계 ${Math.round(total)} ms, 최근 ${s.window_s}초)` : '';
  if (!total) { box.append(el('span', {class: 'empty'}, '아직 완료된 프레임이 없습니다. 촬영을 시작하면 표시됩니다.')); }
  const width = box.clientWidth || 600;
  for (const st of vals) {
    const v = Math.max(0, st.v ?? 0);
    if (!total) break;
    const seg = el('div', {class: `seg s-${st.key}${st.key === 'gesture' ? ' dark-ink' : ''}`, tabindex: '0'});
    seg.style.flex = `${v} 0 0`;
    if ((v / total) * width >= 56) seg.append(el('span', {}, `${Math.round(v)} ms`));
    const html = `<b>${st.name}</b><br>${st.desc}<br>p50 ${msPrecise(st.v)} · p95 ${msPrecise(st.p95)} · n ${st.n}`;
    seg.addEventListener('mousemove', e => showTip(html, e.clientX, e.clientY));
    seg.addEventListener('mouseleave', hideTip);
    seg.addEventListener('focus', () => { const r = seg.getBoundingClientRect(); showTip(html, r.left, r.top); });
    seg.addEventListener('blur', hideTip);
    box.append(seg);
  }
  const legend = $('legend');
  legend.replaceChildren(...vals.map(st => {
    const key = el('span', {class: 'key'});
    key.append(el('i', {class: `swatch s-${st.key}`}), `${st.name} ${msPrecise(st.v)}`);
    return key;
  }));
}

// Round tick step (1/2/5 × 10^n) for about four intervals, and the axis max as a whole number of steps.
function niceScale(v) {
  const raw = Math.max(v || 0, 20) / 4;
  const p = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 5, 10].map(m => m * p).find(m => m >= raw);
  return {step, max: step * Math.ceil(Math.max(v || 0, 20) / step)};
}

function renderTrend(s) {
  const box = $('trend');
  const hist = s.history;
  const W = box.clientWidth || 800, H = 240, L = 48, R = 12, T = 10, B = 26;
  const {step, max: vmax} = niceScale(Math.max(0, ...hist.map(h => h.e2e_p95 ?? 0)) * 1.05);
  const x = i => L + (i / Math.max(1, hist.length - 1)) * (W - L - R);
  const y = v => T + (1 - v / vmax) * (H - T - B);
  const root = svg('svg', {viewBox: `0 0 ${W} ${H}`, role: 'img', 'aria-label': '촬영에서 canvas까지 지연의 p50과 p95 추이 (ms)'});
  const grid = svg('g', {class: 'grid'}), axis = svg('g', {class: 'axis'});
  for (let v = 0; v <= vmax + 1e-9; v += step) {
    const yy = y(v);
    if (v) grid.append(svg('line', {x1: L, x2: W - R, y1: yy, y2: yy}));
    const t = svg('text', {x: L - 6, y: yy + 4, 'text-anchor': 'end'}); t.textContent = `${Math.round(v)}`; axis.append(t);
  }
  for (const sec of [120, 90, 60, 30, 0]) {
    const i = hist.length - 1 - sec;
    if (i < 0) continue;
    const t = svg('text', {x: x(i), y: H - 6, 'text-anchor': 'middle'}); t.textContent = sec ? `-${sec}s` : '지금'; axis.append(t);
  }
  root.append(grid, svg('line', {class: 'baseline', x1: L, x2: W - R, y1: y(0), y2: y(0)}), axis);
  for (const [key, color] of [['e2e_p95', 'var(--s-p95)'], ['e2e_p50', 'var(--s-p50)']]) {
    let d = '', pen = false;
    hist.forEach((h, i) => {
      const v = h[key];
      if (v === null) { pen = false; return; }
      d += `${pen ? 'L' : 'M'}${x(i).toFixed(1)},${y(Math.max(0, v)).toFixed(1)}`; pen = true;
    });
    root.append(svg('path', {class: 'series', d, stroke: color}));
  }
  const cross = svg('line', {class: 'crosshair', y1: T, y2: H - B, visibility: 'hidden'});
  const dots = ['var(--s-p50)', 'var(--s-p95)'].map(c => svg('circle', {r: 4, fill: c, visibility: 'hidden'}));
  const hit = svg('rect', {x: L, y: T, width: W - L - R, height: H - T - B, fill: 'transparent'});
  root.append(cross, ...dots, hit);
  hit.addEventListener('mousemove', e => {
    const r = root.getBoundingClientRect();
    const px = (e.clientX - r.left) * (W / r.width);
    const i = Math.max(0, Math.min(hist.length - 1, Math.round(((px - L) / (W - L - R)) * (hist.length - 1))));
    const h = hist[i];
    cross.setAttribute('x1', x(i)); cross.setAttribute('x2', x(i)); cross.setAttribute('visibility', 'visible');
    [h.e2e_p50, h.e2e_p95].forEach((v, j) => {
      dots[j].setAttribute('visibility', v === null ? 'hidden' : 'visible');
      if (v !== null) { dots[j].setAttribute('cx', x(i)); dots[j].setAttribute('cy', y(Math.max(0, v))); }
    });
    const ago = hist.length - 1 - i;
    const stages = STAGES.map(st => `${st.name} ${msPrecise(h.stage_p50[st.key])}`).join('<br>');
    showTip(`<b>${ago ? `${ago}초 전` : '지금'}</b> · 프레임 ${h.frames} (완료 ${h.complete})<br>p50 ${msPrecise(h.e2e_p50)} · p95 ${msPrecise(h.e2e_p95)}<br><span class="muted">${stages}</span>`, e.clientX, e.clientY);
  });
  hit.addEventListener('mouseleave', () => { cross.setAttribute('visibility', 'hidden'); dots.forEach(d => d.setAttribute('visibility', 'hidden')); hideTip(); });
  box.replaceChildren(root);
}

function renderStageTable(s) {
  const body = $('stage-table').tBodies[0];
  const rows = [...STAGES, {key: 'server', name: '서버 구간 합계', desc: 'gateway 수신 → canvas'}, {key: 'e2e', name: '전체', desc: '촬영 → canvas'}];
  body.replaceChildren(...rows.map(st => {
    const v = s.stages[st.key], tr = el('tr');
    const name = el('td');
    if (STAGES.includes(st)) name.append(el('i', {class: `swatch s-${st.key}`}));
    name.append(st.name);
    tr.append(name, el('td', {class: 'desc'}, st.desc), el('td', {class: 'num'}, msPrecise(v.p50)), el('td', {class: 'num'}, msPrecise(v.p95)),
      el('td', {class: 'num'}, msPrecise(v.max)), el('td', {class: 'num'}, `${v.n}`));
    return tr;
  }));
}

function badge(level, icon, text) {
  const span = el('span', {class: `badge ${level}`});
  span.append(el('i', {}, icon), text);
  return span;
}

function lossLevel(rate, warn, crit) {
  if (rate === null) return ['none', '–'];
  if (rate >= crit) return ['critical', '✕'];
  if (rate >= warn) return ['warning', '▲'];
  return ['good', '✓'];
}

function renderFlow(s) {
  const body = $('flow-table').tBodies[0];
  const labels = {frames: 'frames (gateway)', landmarks: 'landmarks (vision)', gestures: 'gestures (gesture)', canvas: 'canvas (canvas)'};
  body.replaceChildren(...Object.keys(labels).map(k => {
    const tr = el('tr');
    tr.append(el('td', {}, labels[k]), el('td', {class: 'num'}, `${s.fps[k]}`), el('td', {class: 'num'}, `${s.received[k]}`),
      el('td', {class: 'num'}, `${s.unmatched[k]}`));
    return tr;
  }));
  const items = [
    ['vision 드롭률', s.rates.vision_drop, 0.1, 0.3, 'vision이 처리 속도를 넘는 프레임을 버린 비율 (latest-wins)'],
    ['gesture 미발행', s.rates.gesture_missing, 0.001, 0.05, 'landmarks는 있는데 gestures가 없음'],
    ['canvas 미발행', s.rates.canvas_missing, 0.001, 0.05, 'gestures는 있는데 canvas 결과가 없음'],
  ];
  $('losses').replaceChildren(...items.map(([name, rate, warn, crit, hint]) => {
    const [level, icon] = lossLevel(rate, warn, crit);
    const li = el('li');
    li.append(badge(level, icon, `${name} ${pct(rate)}`), el('span', {class: 'desc'}, ` · ${hint}`));
    return li;
  }));
}

function renderVision(s) {
  const body = $('vision-table').tBodies[0];
  const v = s.vision;
  if (!v) { $('vision-caption').textContent = '(VISION_METRICS_URL 미설정)'; body.replaceChildren(); return; }
  $('vision-caption').textContent = v.ok
    ? `(vision /metrics · 입력 ${v.input_fps} FPS · 처리 ${v.processing_fps} FPS · 최근 1000프레임)`
    : `(${v.url} 읽기 실패: ${v.error ?? '대기 중'})`;
  body.replaceChildren(...VISION_ROWS.filter(([k]) => v.timings_ms[k]).map(([k, label]) => {
    const t = v.timings_ms[k], tr = el('tr');
    tr.append(el('td', {}, label), el('td', {class: 'num'}, msPrecise(t.avg)), el('td', {class: 'num'}, msPrecise(t.p50)),
      el('td', {class: 'num'}, msPrecise(t.p95)), el('td', {class: 'num'}, msPrecise(t.max)));
    return tr;
  }));
}

function renderRecent(s) {
  const body = $('recent-table').tBodies[0];
  body.replaceChildren(...s.recent.map(r => {
    const tr = el('tr');
    const level = r.outcome === 'complete' ? ['good', '✓'] : ['warning', '▲'];
    const outcome = el('td'); outcome.append(badge(level[0], level[1], OUTCOME_LABEL[r.outcome] ?? r.outcome));
    tr.append(el('td', {class: 'num'}, `${r.frame_id}`), outcome,
      el('td', {}, r.gesture ? `${r.gesture}${r.hand === false ? ' (손 없음)' : ''}` : (r.hand === false ? '손 없음' : '–')),
      ...STAGES.map(st => el('td', {class: 'num'}, msPrecise(r.stages[st.key]))),
      el('td', {class: 'num'}, msPrecise(r.stages.e2e)));
    return tr;
  }));
}

// -- live connection ----------------------------------------------------------------------------
let socket, last;
function render(s) {
  last = s;
  $('settle').textContent = `${(s.settle_ms / 1000).toFixed(0)}`;
  renderStatus(s); renderTiles(s); renderStack(s); renderTrend(s);
  renderStageTable(s); renderFlow(s); renderVision(s); renderRecent(s);
}

function connect() {
  const scheme = location.protocol === 'https:' ? 'wss:' : 'ws:';
  socket = new WebSocket(`${scheme}//${location.host}/ws/live`);
  socket.onopen = () => socket.send(JSON.stringify({window_s: Number($('window').value)}));
  socket.onmessage = e => render(JSON.parse(e.data));
  socket.onclose = () => {
    const box = $('status');
    box.replaceChildren(el('span', {class: 'bad'}, '대시보드 서버 연결 끊김 · 재연결 중'));
    setTimeout(connect, 1500);
  };
}
$('window').onchange = () => { if (socket?.readyState === WebSocket.OPEN) socket.send(JSON.stringify({window_s: Number($('window').value)})); };
window.addEventListener('resize', () => { if (last) { renderStack(last); renderTrend(last); } });
connect();
