const list = document.getElementById('gallery');
const empty = document.getElementById('gallery-empty');
const failure = document.getElementById('gallery-error');
const count = document.getElementById('count');
const REFRESH_MS = 3000;
let signature = '';

// Times come from the server clock as epoch ms, so the browser shows local time even when the
// container runs in UTC (file names use the container clock and may differ).
function formatTime(ms) {
  return new Date(ms).toLocaleString('ko-KR', {month: 'long', day: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit'});
}

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function link(text, href, download) {
  const anchor = element('a', '', text);
  anchor.href = href;
  if (download) anchor.download = download; else { anchor.target = '_blank'; anchor.rel = 'noopener'; }
  return anchor;
}

function card({live, image, title, meta, actions}) {
  const item = element('li', live ? 'gallery-item live' : 'gallery-item');
  const picture = element('img');
  picture.src = image; picture.alt = title; picture.loading = 'lazy';
  const info = element('div', 'gallery-info');
  if (live) info.append(element('span', 'gallery-badge', '촬영 중'));
  info.append(element('p', 'gallery-title', title), element('p', 'gallery-meta', meta));
  const buttons = element('div', 'gallery-actions');
  buttons.append(...actions);
  info.append(buttons);
  item.append(picture, info);
  return item;
}

function liveImage(sessionId) {
  return `/v1/sessions/${encodeURIComponent(sessionId)}/export.png?t=${Date.now()}`;
}

function render(data) {
  const live = data.live && data.live.active ? data.live : null;
  const files = data.files.filter(file => !(live && file.file === live.saved_file));
  const nextSignature = JSON.stringify([files.map(file => file.url), live && [live.session_id, live.has_drawing]]);
  if (nextSignature === signature) {
    // Same list: only refresh the live thumbnail so the newest strokes show up.
    const livePicture = list.querySelector('.gallery-item.live img');
    if (livePicture && live) livePicture.src = liveImage(live.session_id);
    return;
  }
  signature = nextSignature;
  const items = [];
  if (live) {
    const short = live.session_id.slice(0, 8);
    items.push(card({live: true, image: liveImage(live.session_id), title: '지금 촬영 중인 그림',
      meta: `세션 ${short} · ${live.has_drawing ? '촬영이 끝나면 자동으로 저장됩니다' : '아직 그린 선이 없습니다'}`,
      actions: [link('크게 보기', `/v1/sessions/${encodeURIComponent(live.session_id)}/export.png`)]}));
  }
  for (const file of files) {
    items.push(card({live: false, image: file.url, title: `${formatTime(file.saved_ms)} 저장`,
      meta: `세션 ${file.session} · ${file.file}`,
      actions: [link('크게 보기', file.url), link('다운로드', file.url, file.file)]}));
  }
  list.replaceChildren(...items);
  count.textContent = `${files.length}개`;
  empty.hidden = items.length > 0;
}

async function refresh() {
  try {
    const response = await fetch('/v1/drawings', {cache: 'no-store'});
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    render(await response.json());
    failure.hidden = true;
  } catch (error) {
    failure.hidden = false;
  }
}

refresh();
setInterval(refresh, REFRESH_MS);
