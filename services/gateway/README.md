# gateway (Container A)

> 상태: **Scaffold** — 애플리케이션 코드 없음. Container는 `sleep infinity`로 대기한다.

| 항목 | 값 |
|---|---|
| Port | `8000` (host 노출: `${GATEWAY_HOST_PORT:-8000}`) |
| 담당자 | _(TBD)_ |
| Dependencies | fastapi, uvicorn, websockets, httpx |

## 책임

- Browser webcam frame 수신 (WebSocket `ws://localhost:8000/ws`)
- Frontend(정적 페이지: webcam, canvas, skeleton overlay) 제공
- `trace_id`, `session_id` 발급, `frame_id` 보존
- 내부 서비스 orchestration: vision → gesture → canvas (순차 호출)
- landmark를 Browser로 전달 (client-side skeleton overlay용)
- Backpressure: session당 in-flight 1개, latest-wins drop
- `gateway_ms`, `*_rtt_ms`, `input_fps`, `vision_fps`, `drop_rate` 측정

## Input / Output

| 방향 | 형식 |
|---|---|
| Browser → gateway | WS binary `[uint32 meta_len][meta JSON][JPEG]` |
| gateway → vision | `POST http://vision:8001/v1/detect` (FrameMessage + image/jpeg) |
| gateway → gesture | `POST http://gesture:8002/v1/classify` (LandmarkMessage) |
| gateway → canvas | `POST http://canvas:8003/v1/apply` (GestureMessage) |
| gateway → Browser | WS JSON `result` (landmarks + gesture + canvas + timings) |

Contract: [`docs/api-contract.md`](../../docs/api-contract.md)

## Rules

- Camera image는 **vision으로만** 보낸다. gesture/canvas/로그에 이미지를 남기지 않는다.
- 내부 서비스 응답의 `trace_id`/`frame_id`가 요청과 다르면 오류로 처리한다.

## 디렉터리

```
src/     # application code (예정)
tests/   # tests (예정)
```

## 개발

```bash
docker compose up -d gateway
docker compose exec gateway bash
```
