import {
  frameRotation, gravityAngle, gravitySign, normalize, smoothGravity, snapAngle, tiltDeadzone, tiltFrom, uprightLayout,
} from './rotation.js';

const $ = id => document.getElementById(id);
const video = $('video');
const canvas = document.createElement('canvas');
const context = canvas.getContext('2d');
let socket, stream, timer, pinger, running = false, busy = false;
let offset = 0, bestRtt = Infinity, reconnectTimer;
let deviceAngle = null, sign = null, gravity = null, tiltActive = false, shownRotation = null;

const screenAngle = () => normalize(screen.orientation?.angle ?? window.orientation ?? 0);

function onMotion(event) {
  const g = event.accelerationIncludingGravity;
  if (!g) return;
  sign ??= gravitySign(g.x, g.y, screenAngle());
  if (sign === null) return;
  gravity = smoothGravity(gravity, g.x, g.y);
  deviceAngle = snapAngle(gravityAngle(gravity.x, gravity.y, sign), deviceAngle);
}

// How to turn the frame back so it is upright: `rotation` in 90° steps (manual choice, or device
// turn beyond the screen) plus, in auto mode, the remaining diagonal `tilt` (cropped).
function currentRotation() {
  const mode = $('rotation').value;
  let rotation = Number(mode), tilt = 0;
  if (mode === 'auto') {
    rotation = frameRotation(deviceAngle, screenAngle());
    if ($('tilt').checked && gravity) {
      ({tilt, active: tiltActive} = tiltDeadzone(tiltFrom(gravityAngle(gravity.x, gravity.y, sign), deviceAngle), tiltActive));
    }
  }
  const label = `회전 보정 ${rotation}°${tilt ? ` · 기울기 ${tilt > 0 ? '+' : ''}${tilt}°` : ''}`
    + (mode === 'auto' ? (deviceAngle === null ? ' (센서 없음)' : ' (자동)') : ' (수동)');
  if (label !== shownRotation) { shownRotation = label; $('orientation').textContent = label; }
  return {rotation, tilt};
}

async function enableMotion() {
  // iOS asks for permission, and only from a user gesture (the start button).
  if (typeof window.DeviceMotionEvent?.requestPermission === 'function') {
    try { if (await window.DeviceMotionEvent.requestPermission() !== 'granted') return; } catch { return; }
  }
  window.addEventListener('devicemotion', onMotion);
}

function setStatus(text, state = 'off') {
  $('status').textContent = text;
  $('status').className = state;
}

function sendPing() {
  if (socket?.readyState === WebSocket.OPEN) socket.send(JSON.stringify({type: 'ping', t0: Date.now()}));
}

function connect() {
  const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
  socket = new WebSocket(`${protocol}//${location.host}/ws/stream`);
  socket.binaryType = 'arraybuffer';
  socket.onopen = () => {
    bestRtt = Infinity;
    setStatus('연결됨', 'on');
    sendPing(); clearInterval(pinger); pinger = setInterval(sendPing, 5000);
  };
  socket.onmessage = event => {
    try {
      const message = JSON.parse(event.data);
      if (message.type === 'pong') {
        const now = Date.now(), rtt = now - message.t0;
        if (rtt < bestRtt) { bestRtt = rtt; offset = message.server_ms - (message.t0 + now) / 2; }
      } else if (message.message_type === 'error') {
        $('message').textContent = `${message.code}: ${message.message}`;
      }
    } catch { $('message').textContent = '서버 응답을 읽을 수 없습니다.'; }
  };
  socket.onclose = () => {
    clearInterval(pinger);
    if (running) { setStatus('재연결 중', 'off'); reconnectTimer = setTimeout(connect, 1000); }
    else setStatus('연결 대기 중', 'off');
  };
  socket.onerror = () => { $('message').textContent = '서버에 연결할 수 없습니다.'; };
}

function sendFrame() {
  if (!running || !socket || socket.readyState !== WebSocket.OPEN || busy || socket.bufferedAmount > 0 || !video.videoWidth) return;
  busy = true;
  const width = Number($('width').value);
  const height = Math.round(video.videoHeight * width / video.videoWidth);
  const {rotation, tilt} = currentRotation();
  const layout = uprightLayout(width, height, rotation, tilt);
  canvas.width = layout.canvasWidth; canvas.height = layout.canvasHeight;  // also resets the transform
  context.translate(canvas.width / 2, canvas.height / 2);
  context.rotate(layout.radians);
  // Zoom in for a diagonal tilt so the turned frame still fills the canvas (corners cropped).
  // drawImage samples the full-resolution video through the transform, so this does not blur twice.
  context.scale(layout.zoom, layout.zoom);
  context.drawImage(video, -width / 2, -height / 2, width, height);
  const capturedAt = Math.round(Date.now() + offset);
  canvas.toBlob(async blob => {
    try {
      if (blob && socket?.readyState === WebSocket.OPEN) {
        const timestamp = new ArrayBuffer(8);
        new DataView(timestamp).setBigUint64(0, BigInt(capturedAt));
        socket.send(new Blob([timestamp, blob]));
      }
    } finally { busy = false; }
  }, 'image/jpeg', Number($('quality').value));
}

async function start() {
  $('toggle').disabled = true; $('message').textContent = '';
  try {
    if (!window.isSecureContext || !navigator.mediaDevices) throw new Error('카메라는 HTTPS 주소에서만 사용할 수 있습니다.');
    const motion = enableMotion();  // before any other await so iOS still sees the user gesture
    stream = await navigator.mediaDevices.getUserMedia({audio: false, video: {facingMode: {ideal: 'environment'}, width: {ideal: 1280}, height: {ideal: 720}}});
    video.srcObject = stream; await video.play();
    await motion;
    running = true; connect();
    timer = setInterval(sendFrame, 1000 / Number($('fps').value));
    $('toggle').textContent = '촬영 중지'; $('toggle').className = 'stop';
  } catch (error) { $('message').textContent = error.message; }
  finally { $('toggle').disabled = false; }
}

function stop() {
  running = false; busy = false; clearInterval(timer); clearInterval(pinger); clearTimeout(reconnectTimer);
  socket?.close(); socket = null;
  stream?.getTracks().forEach(track => track.stop()); stream = null; video.srcObject = null;
  window.removeEventListener('devicemotion', onMotion);
  deviceAngle = null; sign = null; gravity = null; tiltActive = false; shownRotation = null; $('orientation').textContent = '';
  $('toggle').textContent = '촬영 시작'; $('toggle').className = '';
}

$('toggle').onclick = () => running ? stop() : start();
$('fps').onchange = () => { if (running) { clearInterval(timer); timer = setInterval(sendFrame, 1000 / Number($('fps').value)); } };
window.addEventListener('pagehide', stop);
