const board = document.getElementById('board');
const context = board.getContext('2d');
const strokes = new Map();
const requestedSession = new URLSearchParams(location.search).get('session_id') || 'auto';
let sessionId = requestedSession === 'auto' ? 'default' : requestedSession;
let followLive = requestedSession === 'auto';
const skeleton = document.getElementById('skeleton');
const skeletonContext = skeleton.getContext('2d');
const savedLink = document.getElementById('saved');
let skeletonTimer;
// MediaPipe hand landmark connections (wrist 0, thumb 1-4, index 5-8, middle 9-12, ring 13-16, pinky 17-20).
const HAND_CONNECTIONS = [[0,1],[1,2],[2,3],[3,4],[0,5],[5,6],[6,7],[7,8],[5,9],[9,10],[10,11],[11,12],[9,13],[13,14],[14,15],[15,16],[13,17],[0,17],[17,18],[18,19],[19,20]];
let snapshotSequence = -1;
let controlSocket, tool = 'pen', strokeId, drawing = false;
const cursor = document.getElementById('cursor');
const penSize = document.getElementById('pen-size');
const eraserSize = document.getElementById('eraser-size');
const penColorPicker = document.getElementById('pen-color-picker');
const penColorHex = document.getElementById('pen-color-hex');
const colorPreview = document.getElementById('color-preview');
let penColor = '#172554';

function activeSize() { return Number(tool === 'pen' ? penSize.value : eraserSize.value); }

function selectTool(nextTool) {
  tool = nextTool;
  document.getElementById('pen').classList.toggle('selected', tool === 'pen');
  document.getElementById('eraser').classList.toggle('selected', tool === 'eraser');
  document.querySelector('.tools').dataset.tool = tool;
}

function normaliseHex(value) {
  const hex = value.trim();
  return /^#[0-9a-fA-F]{6}$/.test(hex) ? hex.toUpperCase() : null;
}

function setPenColor(value) {
  const color = normaliseHex(value);
  if (!color) return false;
  penColor = color;
  penColorPicker.value = color;
  penColorHex.value = color;
  colorPreview.style.backgroundColor = color;
  for (const swatch of document.querySelectorAll('.color-swatch')) {
    swatch.classList.toggle('selected', swatch.dataset.color.toUpperCase() === color);
  }
  return true;
}

// Strokes are kept in fixed canvas coordinates on an offscreen page; the visible board shows that
// page through the zoom view (screen = canvas * scale + offset) sent by the server as set_view.
const page = document.createElement('canvas');
page.width = board.width; page.height = board.height;
const pageContext = page.getContext('2d');
const IDENTITY = {scale: 1, offset: {x: 0, y: 0}};
let view = IDENTITY;

function setView(next) {
  view = next && next.scale > 0 && next.offset ? next : IDENTITY;
  const zoom = document.getElementById('zoom');
  zoom.hidden = Math.abs(view.scale - 1) < 0.01;
  zoom.textContent = `확대 ${view.scale.toFixed(1)}×`;
}

function draw() {
  pageContext.clearRect(0, 0, page.width, page.height);
  pageContext.lineCap = pageContext.lineJoin = 'round';
  for (const stroke of strokes.values()) {
    if (!stroke.points.length) continue;
    const [first, ...rest] = stroke.points;
    pageContext.globalCompositeOperation = stroke.erase ? 'destination-out' : 'source-over';
    pageContext.strokeStyle = pageContext.fillStyle = stroke.style.color || '#172554';
    pageContext.lineWidth = stroke.style.width || (stroke.erase ? 30 : 4);
    pageContext.beginPath(); pageContext.moveTo(first.x, first.y);
    if (rest.length) { for (const point of rest) pageContext.lineTo(point.x, point.y); pageContext.stroke(); }
    else { pageContext.arc(first.x, first.y, pageContext.lineWidth / 2, 0, Math.PI * 2); pageContext.fill(); }
  }
  pageContext.globalCompositeOperation = 'source-over';
  context.setTransform(1, 0, 0, 1, 0, 0);
  context.fillStyle = '#cbd5e1';  // area outside the page, visible only when zoomed out
  context.fillRect(0, 0, board.width, board.height);
  context.setTransform(view.scale, 0, 0, view.scale, view.offset.x, view.offset.y);
  context.fillStyle = '#ffffff';
  context.fillRect(0, 0, page.width, page.height);
  context.drawImage(page, 0, 0);
  context.setTransform(1, 0, 0, 1, 0, 0);
}

function apply(operations, reset = false) {
  if (reset) strokes.clear();
  for (const operation of operations) {
    if (operation.type === 'clear') strokes.clear();
    else if (operation.type.endsWith('_begin')) strokes.set(operation.stroke_id, {points: [...operation.points], style: operation.style || {}, erase: operation.type === 'erase_begin'});
    else if (operation.type.endsWith('_append')) strokes.get(operation.stroke_id)?.points.push(...operation.points);
    else if (operation.type === 'remove_stroke') strokes.delete(operation.stroke_id);
    else if (operation.type === 'set_view') setView(operation.view);
  }
  draw();
}

function clearSkeleton() { skeletonContext.clearRect(0, 0, skeleton.width, skeleton.height); }

function drawSkeleton(hands) {
  clearSkeleton();
  clearTimeout(skeletonTimer);
  if (!hands.length) return;
  skeletonContext.lineWidth = 3;
  skeletonContext.strokeStyle = 'rgba(14, 116, 144, 0.75)';
  for (const hand of hands) {
    const at = index => [hand[index][0] * skeleton.width, hand[index][1] * skeleton.height];
    skeletonContext.beginPath();
    for (const [from, to] of HAND_CONNECTIONS) { skeletonContext.moveTo(...at(from)); skeletonContext.lineTo(...at(to)); }
    skeletonContext.stroke();
    hand.forEach((_, index) => {
      const [x, y] = at(index);
      skeletonContext.fillStyle = index === 8 ? 'rgba(220, 38, 38, 0.9)' : 'rgba(8, 145, 178, 0.85)';
      skeletonContext.beginPath(); skeletonContext.arc(x, y, index === 8 ? 7 : 4, 0, Math.PI * 2); skeletonContext.fill();
    });
  }
  // Vision publishes every frame; if frames stop the stale skeleton should not stay on screen.
  skeletonTimer = setTimeout(clearSkeleton, 500);
}

function setStatus(live) {
  const mode = followLive ? '자동' : '고정';
  document.getElementById('status').textContent = `연결됨 · ${mode} · ${sessionId}${live ? ' · 촬영 중' : ''}`;
}

function showSaved(file) {
  savedLink.hidden = !file;
  if (file) { savedLink.textContent = `저장됨: ${file}`; savedLink.href = `/drawings/${encodeURIComponent(file)}`; }
}

// Gesture V-sign / palm choices arrive as 'tool' messages: mirror them on the palette and sliders.
function setSlider(input, output, value) {
  const clamped = Math.max(Number(input.min), Math.min(Number(input.max), Math.round(value)));
  input.value = clamped;
  document.getElementById(output).textContent = `${clamped} px`;
}

function applyTool(message) {
  if (message.color) setPenColor(message.color);
  if (message.brush_size) setSlider(penSize, 'pen-size-value', message.brush_size);
  if (message.eraser_size) setSlider(eraserSize, 'eraser-size-value', message.eraser_size);
}

function switchSession(message) {
  const changed = message.session_id !== sessionId;
  sessionId = message.session_id; followLive = message.auto;
  setStatus(message.live); showSaved(message.saved); clearSkeleton();
  if (changed && controlSocket) { controlSocket.onclose = null; controlSocket.close(); connectControls(); }
}

function connect() {
  const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
  const socket = new WebSocket(`${protocol}//${location.host}/ws/board?session_id=${encodeURIComponent(followLive ? 'auto' : sessionId)}`);
  socket.onopen = () => setStatus(false);
  socket.onmessage = event => {
    const message = JSON.parse(event.data);
    if (message.type === 'session') { switchSession(message); return; }
    if (message.session_id !== undefined && message.session_id !== sessionId) return;
    if (message.type === 'landmarks') { drawSkeleton(message.hands); return; }
    if (message.type === 'saved') { showSaved(message.file); return; }
    if (message.type === 'tool') { applyTool(message); return; }
    if (message.type === 'session_end') { setStatus(false); clearSkeleton(); return; }
    if (message.type === 'snapshot') { snapshotSequence = message.sequence; setView(message.view); apply(message.operations, true); return; }
    if (message.sequence <= snapshotSequence) return;
    snapshotSequence = message.sequence; apply(message.operations);
  };
  socket.onclose = () => { document.getElementById('status').textContent = '재연결 중'; setTimeout(connect, 1000); };
}

function point(event) {
  const box = board.getBoundingClientRect();
  return {x: Math.max(0, Math.min(1, (event.clientX - box.left) / box.width)), y: Math.max(0, Math.min(1, (event.clientY - box.top) / box.height))};
}

function updateCursor(event) {
  const box = board.getBoundingClientRect();
  // The pointer circle shows the real footprint, which grows with the zoom.
  const size = activeSize() * view.scale * box.width / board.width;
  cursor.style.left = `${event.clientX - box.left}px`;
  cursor.style.top = `${event.clientY - box.top}px`;
  cursor.style.width = cursor.style.height = `${Math.max(size, 4)}px`;
  cursor.classList.toggle('eraser-cursor', tool === 'eraser');
  cursor.style.display = 'block';
}

function drawingParams() {
  const size = activeSize();
  return tool === 'pen' ? {stroke_id: strokeId, color: penColor, brush_size: size} : {stroke_id: strokeId, eraser_radius: size / (Math.min(board.width, board.height) * 2)};
}

function sendDraw(command, event) {
  if (controlSocket?.readyState === WebSocket.OPEN) controlSocket.send(JSON.stringify({type: 'gesture', gesture: command, pointer: point(event), params: drawingParams()}));
}

function connectControls() {
  const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
  controlSocket = new WebSocket(`${protocol}//${location.host}/ws/control?session_id=${encodeURIComponent(sessionId)}`);
  controlSocket.onmessage = event => { const message = JSON.parse(event.data); if (message.type === 'error') document.getElementById('status').textContent = message.message; };
  controlSocket.onclose = () => setTimeout(connectControls, 1000);
}

document.getElementById('pen').onclick = () => selectTool('pen');
document.getElementById('eraser').onclick = () => selectTool('eraser');
penSize.oninput = event => { document.getElementById('pen-size-value').textContent = `${event.target.value} px`; };
eraserSize.oninput = event => { document.getElementById('eraser-size-value').textContent = `${event.target.value} px`; };
penColorPicker.oninput = event => setPenColor(event.target.value);
penColorHex.onchange = () => { if (!setPenColor(penColorHex.value)) penColorHex.value = penColor; };
penColorHex.onkeydown = event => { if (event.key === 'Enter') { event.preventDefault(); penColorHex.blur(); } };
for (const swatch of document.querySelectorAll('.color-swatch')) {
  swatch.style.backgroundColor = swatch.dataset.color;
  swatch.onclick = () => setPenColor(swatch.dataset.color);
}
board.addEventListener('pointerdown', event => { if (event.button !== 0) return; updateCursor(event); board.setPointerCapture(event.pointerId); drawing = true; strokeId = crypto.randomUUID(); sendDraw(tool === 'pen' ? 'DRAW_START' : 'ERASE_START', event); });
board.addEventListener('pointermove', event => { updateCursor(event); if (drawing) sendDraw(tool === 'pen' ? 'DRAW_MOVE' : 'ERASE_MOVE', event); });
board.addEventListener('pointerup', event => { if (!drawing) return; drawing = false; sendDraw(tool === 'pen' ? 'DRAW_END' : 'ERASE_END', event); });
board.addEventListener('pointercancel', event => { if (drawing) { drawing = false; sendDraw(tool === 'pen' ? 'DRAW_END' : 'ERASE_END', event); } });
board.addEventListener('pointerleave', () => { if (!drawing) cursor.style.display = 'none'; });
connect();
connectControls();
selectTool('pen');
setPenColor(penColor);
