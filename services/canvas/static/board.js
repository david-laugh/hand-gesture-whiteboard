const board = document.getElementById('board');
const context = board.getContext('2d');
const strokes = new Map();
const sessionId = new URLSearchParams(location.search).get('session_id') || 'default';
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

function draw() {
  context.clearRect(0, 0, board.width, board.height);
  context.lineCap = context.lineJoin = 'round';
  for (const stroke of strokes.values()) {
    if (!stroke.points.length) continue;
    const [first, ...rest] = stroke.points;
    context.globalCompositeOperation = stroke.erase ? 'destination-out' : 'source-over';
    context.strokeStyle = context.fillStyle = stroke.style.color || '#172554';
    context.lineWidth = stroke.style.width || (stroke.erase ? 30 : 4);
    context.beginPath(); context.moveTo(first.x, first.y);
    if (rest.length) { for (const point of rest) context.lineTo(point.x, point.y); context.stroke(); }
    else { context.arc(first.x, first.y, context.lineWidth / 2, 0, Math.PI * 2); context.fill(); }
  }
  context.globalCompositeOperation = 'source-over';
}

function apply(operations, reset = false) {
  if (reset) strokes.clear();
  for (const operation of operations) {
    if (operation.type === 'clear') strokes.clear();
    else if (operation.type.endsWith('_begin')) strokes.set(operation.stroke_id, {points: [...operation.points], style: operation.style || {}, erase: operation.type === 'erase_begin'});
    else if (operation.type.endsWith('_append')) strokes.get(operation.stroke_id)?.points.push(...operation.points);
    else if (operation.type === 'remove_stroke') strokes.delete(operation.stroke_id);
  }
  draw();
}

function connect() {
  const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
  const socket = new WebSocket(`${protocol}//${location.host}/ws/board?session_id=${encodeURIComponent(sessionId)}`);
  socket.onopen = () => { document.getElementById('status').textContent = `연결됨 · ${sessionId}`; };
  socket.onmessage = event => {
    const message = JSON.parse(event.data);
    if (message.type === 'snapshot') { snapshotSequence = message.sequence; apply(message.operations, true); return; }
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
  const size = activeSize() * box.width / board.width;
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
