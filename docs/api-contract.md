# API Contract

> 상태: **Scaffold / Draft v1.0.0**. Endpoint는 계획이며 아직 구현되지 않았다.
> JSON Schema 원본: [`shared/contracts/`](../shared/contracts) · 예시: [`shared/examples/`](../shared/examples)

## 1. 공통 Envelope

모든 message는 다음 field를 **반드시** 포함한다.

| Field | Type | 설명 |
|---|---|---|
| `schema_version` | string (semver) | Contract 버전. Major가 다르면 수신 측은 거부(HTTP 422)한다. |
| `message_type` | string const | `frame` / `landmarks` / `gesture` / `canvas` |
| `trace_id` | string | **frame 1개당 1개**, gateway가 발급(UUIDv4 권장). 모든 서비스가 그대로 전달. |
| `session_id` | string | Browser WebSocket 연결 단위 ID, gateway가 발급. |
| `frame_id` | integer ≥ 0 | session 내 단조 증가 frame 번호. |
| `timestamp_ms` | integer | 해당 message를 **생성한 서비스**의 Unix epoch ms. |

### Tracing 규칙

- `trace_id` + `frame_id`로 한 frame이 gateway → vision → gesture → canvas → browser로 이동하는 전 과정을 추적한다.
- 수신한 `trace_id` / `session_id` / `frame_id`를 **절대 재발급·수정하지 않는다**. 응답 message에 그대로 복사한다.
- 내부 HTTP 호출에는 동일 값을 header로도 전달한다 (로그/프록시 용):
  - `X-Trace-Id`, `X-Session-Id`, `X-Frame-Id`
- 모든 서비스 로그는 한 줄에 `trace_id`, `frame_id`, `service`, `*_ms`를 포함하는 JSON 형식을 권장한다.

## 2. Message 정의

| Message | 방향 | Schema | 이미지 포함 |
|---|---|---|---|
| Frame | gateway → vision | `frame.schema.json` | ✅ (별도 binary part) |
| Landmarks | vision → gateway, gateway → gesture, gateway → browser | `landmark.schema.json` | ❌ |
| Gesture | gesture → gateway, gateway → canvas | `gesture.schema.json` | ❌ |
| Canvas | canvas → gateway, gateway → browser | `canvas.schema.json` | ❌ |

### 2.1 Frame (`frame.schema.json`)

Frame metadata JSON + JPEG binary. JSON 안에 base64로 이미지를 넣지 **않는다** (payload 33% 증가 방지).

주요 field: `content_type`(=`image/jpeg`), `width`, `height`, `captured_at_ms`, `byte_length`, `received_at_ms`(gateway), `jpeg_quality`, `mirrored`.

### 2.2 Landmarks (`landmark.schema.json`)

- `hands[]` (0~2개): `hand_index`, `handedness`(`Left`/`Right`), `confidence`, `landmarks[]`
- `landmarks[]`: **정확히 21개**, `id`(0~20), normalized `x`, `y`, `z`
- `image_width`, `image_height`, `vision_ms`

Landmark id는 MediaPipe 정의를 따른다: 0 WRIST, 4 THUMB_TIP, 8 INDEX_FINGER_TIP, 12 MIDDLE_FINGER_TIP, 16 RING_FINGER_TIP, 20 PINKY_TIP.

### 2.3 Gesture (`gesture.schema.json`)

- `gesture`: `IDLE`, `DRAW_START|MOVE|END`, `ERASE_START|MOVE|END`, `ZOOM_START|UPDATE|END`, `UNDO`, `REDO`, `CLEAR`
- `pointer`: normalized `{x, y}` 또는 `null`
- `confidence`, `hand_index`, `params`(`stroke_id`, `zoom_scale`, `zoom_center`, `eraser_radius`, `color`, `brush_size`, `state_frames`), `gesture_ms`

### 2.4 Canvas (`canvas.schema.json`)

- `sequence`: session 내 단조 증가. Browser는 gap 발견 시 resync 요청(향후).
- `operations[]`: `type` + `stroke_id` / `points`(canvas pixel 좌표) / `style` / `view`
  - `stroke_begin|append|end`, `erase_begin|append|end`, `remove_stroke`, `restore_stroke`, `set_view`, `clear`, `noop`
- `history`: `undo_depth`, `redo_depth`
- `canvas_ms`

## 3. Transport (계획)

### 3.1 Browser ↔ gateway (WebSocket)

- URL: `ws://localhost:8000/ws`
- **Browser → gateway**: binary message 1개 = 1 frame

  ```
  [uint32 BE: meta_len][meta JSON (utf-8, meta_len bytes)][JPEG bytes]
  ```

  meta JSON은 browser가 아는 field만 포함: `frame_id`, `captured_at_ms`, `width`, `height`, `jpeg_quality`, `mirrored`.
  `trace_id`, `session_id`, `received_at_ms`는 gateway가 채운다.

- **gateway → Browser**: text(JSON) message

  ```json
  {
    "schema_version": "1.0.0",
    "message_type": "result",
    "trace_id": "...", "session_id": "...", "frame_id": 1042, "timestamp_ms": 0,
    "landmarks": { "...": "LandmarkMessage" },
    "gesture":   { "...": "GestureMessage" },
    "canvas":    { "...": "CanvasMessage" },
    "timings": {
      "gateway_ms": 0, "vision_ms": 0, "gesture_ms": 0, "canvas_ms": 0,
      "vision_rtt_ms": 0, "gesture_rtt_ms": 0, "canvas_rtt_ms": 0,
      "server_total_ms": 0
    }
  }
  ```
  (`result` envelope schema는 gateway 구현 시 `shared/contracts/`에 추가 예정)

### 3.2 gateway → 내부 서비스 (HTTP/1.1, JSON)

| Service | Method & Path | Request | Response |
|---|---|---|---|
| vision | `POST /v1/detect` | `multipart/form-data`: part `meta` (application/json, FrameMessage) + part `image` (image/jpeg) | `LandmarkMessage` |
| gesture | `POST /v1/classify` | `LandmarkMessage` | `GestureMessage` |
| canvas | `POST /v1/apply` | `GestureMessage` | `CanvasMessage` |
| canvas | `GET /v1/sessions/{session_id}/export.png` | – | `image/png` (최종 PNG export) |
| canvas | `POST /v1/sessions/{session_id}/reset` | – | `204` |
| gesture | `POST /v1/sessions/{session_id}/reset` | – | `204` |
| 모든 서비스 | `GET /healthz` | – | `{"status":"ok","service":"<name>"}` |

### 3.3 오류 응답 (공통)

```json
{
  "schema_version": "1.0.0",
  "message_type": "error",
  "trace_id": "...", "session_id": "...", "frame_id": 1042, "timestamp_ms": 0,
  "service": "vision",
  "code": "INVALID_IMAGE",
  "message": "JPEG decode failed"
}
```

| HTTP | code 예시 | 의미 |
|---|---|---|
| 400 | `INVALID_IMAGE` | JPEG decode 실패 |
| 422 | `SCHEMA_VIOLATION`, `SCHEMA_VERSION_MISMATCH` | contract 위반 |
| 503 | `NOT_READY` | 모델 미로딩 등 |

Gateway는 내부 오류 발생 시 해당 frame을 drop 처리하고 Browser에는 `error` message를 전달한다.

## 4. Versioning

- `MAJOR`: 호환되지 않는 변경 (field 삭제/의미 변경) → 모든 서비스 동시 업데이트
- `MINOR`: optional field 추가
- `PATCH`: 문서/설명 변경
- Schema 변경은 PR로만 하며, `shared/examples/`가 schema를 통과해야 한다.
