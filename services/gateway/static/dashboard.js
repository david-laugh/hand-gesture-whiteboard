const $ = id => document.getElementById(id);
let previousPreview;

function connectPreview() {
  const scheme = location.protocol === 'https:' ? 'wss:' : 'ws:';
  const socket = new WebSocket(`${scheme}//${location.host}/ws/status`);
  socket.binaryType = 'blob';
  socket.onmessage = event => {
    const url = URL.createObjectURL(event.data);
    const image = $('latest-frame');
    image.onload = () => { if (previousPreview) URL.revokeObjectURL(previousPreview); previousPreview = url; };
    image.src = url; image.classList.add('visible'); $('preview-empty').hidden = true;
  };
  socket.onclose = () => setTimeout(connectPreview, 1000);
}

async function refresh() {
  try {
    const [healthResponse, metricsResponse, infoResponse] = await Promise.all([fetch('/healthz'), fetch('/api/metrics'), fetch('/api/info')]);
    const health = await healthResponse.json();
    const metrics = await metricsResponse.json();
    const info = await infoResponse.json();
    $('connection').textContent = health.mqtt_connected ? 'MQTT 연결됨' : 'MQTT 연결 안 됨';
    $('connection').className = health.mqtt_connected ? 'state ok' : 'state error';
    $('received').textContent = metrics.received || 0;
    $('published').textContent = metrics.published || 0;
    $('dropped').textContent = metrics.dropped || 0;
    $('camera-link').href = info.camera_url;
    $('camera-url').textContent = info.camera_url;
  } catch {
    $('connection').textContent = 'Gateway에 연결할 수 없음';
    $('connection').className = 'state error';
  }
}
refresh();
setInterval(refresh, 2000);
connectPreview();
