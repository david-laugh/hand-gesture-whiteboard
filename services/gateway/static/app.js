const $ = id => document.getElementById(id);
const capture = document.createElement('canvas');
const ctx = capture.getContext('2d');
let stream, socket, timer, running = false, generation = 0, frameId = 0;
function stop(message = '중지됨') {
  running = false; generation++; clearTimeout(timer);
  stream?.getTracks().forEach(track => track.stop()); stream = null;
  if (socket) { socket.onclose = null; socket.close(); socket = null; }
  $('video').srcObject = null; $('start').disabled = false; $('stop').disabled = true;
  $('status').textContent = message;
}
async function sendFrame(token) {
  if (!running || token !== generation) return;
  try {
    if (socket?.readyState === WebSocket.OPEN && socket.bufferedAmount < 256 * 1024) {
      const video = $('video');
      capture.width = Math.min(960, video.videoWidth);
      capture.height = Math.round(video.videoHeight * capture.width / video.videoWidth);
      ctx.save(); ctx.translate(capture.width, 0); ctx.scale(-1, 1); ctx.drawImage(video, 0, 0, capture.width, capture.height); ctx.restore();
      const capturedAt = Date.now();
      const blob = await new Promise(resolve => capture.toBlob(resolve, 'image/jpeg', 0.7));
      if (!blob) throw new Error('카메라 프레임을 인코딩할 수 없습니다.');
      const jpeg = new Uint8Array(await blob.arrayBuffer());
      if (!running || token !== generation || socket?.readyState !== WebSocket.OPEN) return;
      const id = frameId++;
      const metadata = new TextEncoder().encode(JSON.stringify({frame_id: id, captured_at_ms: capturedAt, width: capture.width, height: capture.height, jpeg_quality: 70, mirrored: true}));
      const packet = new Uint8Array(4 + metadata.length + jpeg.length);
      new DataView(packet.buffer).setUint32(0, metadata.length); packet.set(metadata, 4); packet.set(jpeg, 4 + metadata.length);
      socket.send(packet);
    }
    if (running && token === generation) timer = setTimeout(() => sendFrame(token), 50);
  } catch (error) { stop(error.message); }
}
$('start').onclick = async () => {
  $('start').disabled = true; $('status').textContent = '카메라 권한 확인 중…';
  const token = ++generation;
  try {
    if (!navigator.mediaDevices) throw new Error('카메라를 사용하려면 HTTPS 또는 localhost로 접속하세요.');
    stream = await navigator.mediaDevices.getUserMedia({video: {width: 960, height: 540}, audio: false});
    if (token !== generation) { stream.getTracks().forEach(track => track.stop()); return; }
    $('video').srcObject = stream; await $('video').play();
    $('video').parentElement.style.aspectRatio = `${$('video').videoWidth} / ${$('video').videoHeight}`;
    frameId = 0;
    socket = new WebSocket(`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws`);
    socket.onmessage = event => {
      try {
        const message = JSON.parse(event.data);
        if (message.message_type === 'hello') {
          running = true; $('stop').disabled = false; $('status').textContent = '전송 중'; sendFrame(token);
        } else if (message.message_type === 'error') $('status').textContent = `${message.code}: ${message.message}`;
      } catch (error) { stop(error.message); }
    };
    socket.onclose = () => stop('연결이 종료되었습니다. 카메라 시작으로 새 세션을 여세요.');
    socket.onerror = () => stop('게이트웨이에 연결할 수 없습니다.');
  } catch (error) { stop(error.message); }
};
$('stop').onclick = () => stop();
window.addEventListener('pagehide', () => stop());
