const $ = id => document.getElementById(id);
const video = $('video');
const canvas = document.createElement('canvas');
const context = canvas.getContext('2d');
let socket, stream, timer, pinger, running = false, busy = false;
let offset = 0, bestRtt = Infinity, reconnectTimer;

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
  canvas.width = width; canvas.height = height;
  context.drawImage(video, 0, 0, width, height);
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
    stream = await navigator.mediaDevices.getUserMedia({audio: false, video: {facingMode: {ideal: 'environment'}, width: {ideal: 1280}, height: {ideal: 720}}});
    video.srcObject = stream; await video.play();
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
  $('toggle').textContent = '촬영 시작'; $('toggle').className = '';
}

$('toggle').onclick = () => running ? stop() : start();
$('fps').onchange = () => { if (running) { clearInterval(timer); timer = setInterval(sendFrame, 1000 / Number($('fps').value)); } };
window.addEventListener('pagehide', stop);
