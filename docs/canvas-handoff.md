# Canvas 구현 인수인계

## 다음 작업자용 현황 요약

Canvas(D)는 Gesture(C)의 `hgw/v1/gestures/{session_id}` MQTT 입력을 받아 그림 상태를 만들고, renderer/Gateway가 소비하는 `hgw/v1/canvas/{session_id}` `CanvasMessage`를 QoS 0, retain false로 발행한다. 브라우저 실시간 화면은 `/ws/board?session_id=<id>`이며, 새 browser에도 살아 있는 session의 snapshot을 전달한다.

1. **Canvas 크기와 상태**: 고정 논리 크기는 `1280×720`이다. browser 표시 크기와 무관하게 모든 stroke 좌표를 이 기준으로 저장·보간하므로, 같은 session을 여러 화면에서 열어도 위치가 달라지지 않는다. 상태는 session별 stroke, sequence, undo/redo, view이며 container 메모리에만 있다. container 재시작 시 그림은 사라진다.
2. **그림 UI**: 펜 기본 굵기는 8px, 지우개 기본 굵기는 30px이고 서로 독립적으로 기억한다. 지우개는 클릭 한 번으로도 현재 원형 영역을 지우며, 점선 포인터 크기와 실제 지우개 폭이 같다. 펜은 팔레트·색상 맵·HEX(`#RRGGBB`) 입력을 제공하고 값은 MQTT `params.color`로 전달한다.
3. **점 끊김 보강**: C는 One-Euro filter로 landmark pointer를 보강하고, D의 low-pass smoothing은 C filter와 겹쳐 약 50ms 지연이 생겨 기본값을 끄고(`CANVAS_SMOOTHING=1.0`), 최대 8px 간격의 선형 보간(`CANVAS_SEGMENT_PX=8`)을 추가 적용한다. 끊김이 보이면 먼저 C 발행 주기와 renderer의 전체 `points` 연결을 확인하고, 이후 보간 간격과 smoothing을 조정한다.
4. **현재 전체 연동**: A Gateway → B Vision → C Gesture → D Canvas다. Vision은 MQTT `hgw/v1/landmarks/{session_id}` 또는 `ws://gesture:8002/ws/landmarks`로 C에 입력을 보내며, C는 현재 `IDLE`, `DRAW_START`, `DRAW_MOVE`, `DRAW_END`를 Canvas에 발행한다.
5. **통합 주의사항**: Vision→Gesture의 MQTT와 WebSocket을 동시에 활성화하면 같은 frame이 중복 처리되어 stroke가 두 번 그려질 수 있다. 통합 실행에서는 한 경로만 사용한다. Vision `frame_id`는 latest-wins 처리로 중간 번호가 빠질 수 있고, `hands[].score`는 손 검출 confidence가 아닌 handedness 분류 점수다. 손 없음은 `hands: []`로 오며 C는 진행 중인 stroke를 끝내야 한다.
6. **남은 작업**: C의 지우개·색상·굵기·줌·undo/redo pose/state machine, Gateway의 실제 QR/사용자 session ID 관리, Canvas 상태 영속화와 MQTT snapshot/replay, 운영용 broker TLS·ACL·Compose 구성이 필요하다.
7. **촬영 세션 자동 전환·저장과 손 뼈대 (추가)**: D는 그림과 무관하게 `hgw/v1/landmarks/+`도 구독한다. 처음 보는 session의 landmarks가 오면 촬영 시작으로 보고 자동 모드 그림판(`http://localhost:8003/`, `session_id` 없음)을 그 세션의 빈 캔버스로 바꾸며 이전 세션 그림을 PNG로 저장한다. landmarks가 `CANVAS_SESSION_IDLE_SEC`(기본 5초) 끊기면 촬영 종료로 보고 저장한다. 저장 폴더는 `CANVAS_SAVE_DIR`(기본 `services/canvas/drawings`)이고, `/gallery`에서 저장된 그림을 썸네일 세로 목록으로 보며(맨 위는 촬영 중 세션), 데이터는 `/v1/drawings`다. 같은 landmarks를 0~1 `[x, y]` 21점으로 줄여 `/ws/board`로 보내 그림 위에 손 뼈대를 그린다. Vision이 MQTT 대신 WebSocket으로만 C에 보내는 실행에서는 landmarks topic이 비므로 뼈대가 보이지 않고, 세션 감지는 C의 gesture 메시지로 대신한다. 카메라 비율은 계약에 프레임 크기가 없어 D에서 보정하지 않으며 시연은 가로 촬영으로 한다.
8. **제스처 색·굵기 → 팔레트 동기화 (추가)**: C가 브이·손바닥 인식 직후 `IDLE`의 `params`(color, brush_size, eraser_radius)와 `DRAW_START`/`ERASE_START`의 `params`로 보내는 값이 바뀌면, D가 `/ws/board`에 `tool` 메시지를 보내 그림판 팔레트와 굵기 슬라이더를 같은 값으로 바꾼다.

## 구현 범위

`services/canvas/`만 구현했다. Gateway, Vision, Gesture, `compose.yaml`, 공통 contract 파일은 변경하지 않았다.

Canvas는 Container D다. B(Vision)는 hand landmark 좌표를 만들고, C(Gesture)는 이를 `GestureMessage`의 normalized `pointer`와 `DRAW_*` 등의 명령으로 변환해야 한다. Canvas는 landmark나 JPEG를 직접 받지 않는다.

```text
A Gateway → B Vision → C Gesture → D Canvas
                              │
                  hgw/v1/gestures/{session_id}
                              │
                              ▼
                           Canvas
                              │
                   hgw/v1/canvas/{session_id}
```

## A → B → C → D MQTT 통합 계획

이 절은 전체 팀이 합의해야 할 **계획과 현재 구현 상태**다. `origin/feature/vision`의 MQTT 발행과 `origin/feature/gesture`의 MQTT/웹소켓 수신 구현을 기준으로 2026-10-29에 확인했다. Canvas를 C로 부르지 않고 역할을 명확히 구분한다: **C는 Gesture command producer, D는 Canvas consumer/operation producer**다.

| 순서 | 발행 서비스 → 구독 서비스 | topic / 연결 | 전달 데이터 | 현재 상태 |
| --- | --- | --- | --- | --- |
| 1 | A Gateway → B Vision | `{prefix}/frames/{session_id}` | binary FrameMessage metadata + JPEG | Vision 브랜치에서 MQTT 수신 구현됨 |
| 2 | B Vision → C Gesture | MQTT `{prefix}/landmarks/{session_id}` 또는 WebSocket `ws://gesture:8002/ws/landmarks` | Vision landmark JSON 1개 = 추론 frame 1개 | 두 경로 모두 Gesture 브랜치에서 수신 구현됨 |
| 3 | C Gesture → D Canvas | `{prefix}/gestures/{session_id}` | 레포 `GestureMessage`, QoS 0 / retain false | 현재 `IDLE`, `DRAW_START/MOVE/END` 발행 구현됨 |
| 4 | D Canvas → A/renderer | `{prefix}/canvas/{session_id}` | `CanvasMessage`: sequence, operations, history | Canvas에서 구현됨 |

`{prefix}` 기본값은 `hgw/v1`이다. Vision이 MQTT 발행으로 바뀌더라도 Gesture는 같은 adapter를 거쳐 처리하므로 Canvas 변경은 필요 없다. 다만 capture 시각을 E2E 결과까지 보존할지 여부는 아래의 시간 필드 주의사항을 팀에서 결정해야 한다.

### 1. A(Gateway) → B(Vision): 카메라 입력 계획

1. A는 QR 또는 연결 사용자마다 고유 `session_id`를 만든다. `default`는 로컬 테스트 전용이다.
2. A는 각 frame에 증가하는 `frame_id`, 캡처 시각 `timestamp_ms`, 추적용 `trace_id`를 넣는다.
3. 현재 Vision MQTT 입력은 `[uint32 BE metadata 길이][FrameMessage JSON][JPEG]` binary payload를 사용한다. Gateway는 이 framing을 유지해야 하며, landmark JSON topic과 혼용하지 않는다.
4. B는 최신 frame만 처리하는 drop policy를 둔다. 늦게 도착한 오래된 frame을 모두 처리하면 손 움직임과 그림이 지연된다.

### 2. B(Vision) → C(Gesture): 좌표 계약과 보강 계획

1. B는 선택한 손가락 tip의 좌표, 가능하면 손 전체 landmark, handedness, confidence를 C로 보낸다.
2. 좌표계는 **이미지 폭/높이 기준 normalized 0~1**로 통일한다. C는 mirror된 사용자 미리보기인지 원본 카메라인지 한 번만 변환하고, 같은 축을 D까지 유지한다.
3. B는 손이 없으면 명시적으로 `hands: []`를 발행한다. C는 이를 받아 진행 중인 stroke를 debounce 후 안전하게 종료한다.
4. `hands[].score`는 **handedness(Left/Right) 분류 점수**이며 손 검출 신뢰도가 아니다. Gesture pose quality의 threshold로 사용하지 않는다.
5. Vision은 처리량을 넘는 frame을 latest-wins 방식으로 건너뛸 수 있다. C는 연속 `frame_id`를 가정하지 않고 `ts` 간격과 서버 수신 gap을 사용한다.

### Vision 샘플 기준: C가 실제로 받을 데이터

`origin/feature/vision`의 `services/vision/samples/`를 확인했다. 현재 B가 C에 제공하는 실제 MediaPipe HandLandmarker 결과 형식은 아래와 같다.

```json
{
  "type": "landmarks",
  "session_id": "sample-session-001",
  "frame_id": 1,
  "ts": 1793239995759,
  "hands": [{
    "handedness": "Left",
    "score": 0.9481,
    "landmarks": [[0.3983, 0.7896, -0.0000]]
  }],
  "gesture": null
}
```

1. `hands`의 각 원소에는 `handedness`, `score`, 21개 `[x, y, z]` landmark가 있다. `x/y`는 이미 normalized 좌표이므로 C는 선택한 fingertip의 `x/y`를 Canvas pointer 후보로 쓸 수 있다. `z`는 Canvas pointer에 보내지 않되, C가 gesture 판정에 사용할 수 있다.
2. `no_hand.json`은 `hands: []`, `gesture: null`이다. C는 빈 배열을 받으면 draw를 시작하지 않고, 이미 진행 중인 stroke가 있으면 `DRAW_END` 또는 `ERASE_END`를 한 번 발행한 뒤 idle 상태가 되어야 한다.
3. `continuous_frames.json`은 `frame_id` 100~129의 30개 메시지이며 `ts`가 33ms 간격이다. C의 filter·outlier·stroke lifecycle 회귀 테스트 입력으로 사용한다. 단, 정지 사진 crop 이동 데이터라 손 모양 변화 gesture의 완전한 검증 자료는 아니다.
4. Vision에는 `trace_id`가 없으므로 Gesture adapter가 `{session_id}-{frame_id}` 형식으로 생성한다. C output의 `message_type`은 `"gesture"`, `schema_version`은 `"1.0.0"`으로 채운다.
5. `gesture`는 Vision 샘플에서 `null`이다. 현 단계에서는 C가 landmark를 직접 판정해 draw/erase/idle을 만들며, 이후 B가 gesture hint를 제공해도 C가 최종 Canvas 명령을 결정한다.

> 시간 필드 주의: Vision의 `ts`는 브라우저 캡처 시각이며 C의 필터·지연 경고에 사용된다. 하지만 현재 Gesture 구현이 Canvas에 발행하는 `GestureMessage.timestamp_ms`는 C 처리 시각이다. 원본 캡처 시각을 Canvas output까지 추적해야 한다면 `captured_at_ms` 같은 contract 필드를 공통 schema에 추가하는 별도 변경이 필요하다. 현재는 동일 `session_id`와 `frame_id`, adapter가 만든 `trace_id`로 흐름을 추적한다.

> 중복 입력 주의: Gesture는 MQTT와 WebSocket 입력을 모두 켤 수 있지만 deduplication하지 않는다. Vision이 같은 frame을 두 경로로 동시에 보내면 C가 두 번 처리하고 Canvas에 중복 stroke가 생긴다. 통합 환경에서는 **Vision→Gesture 입력 경로를 MQTT 또는 WebSocket 하나만 활성화**한다. MQTT 전환 검증 시 `WS_INPUT_ENABLED=false` 또는 Vision의 WebSocket 송신 비활성화가 필요하다.

### B payload를 C의 Canvas 입력으로 바꾸는 정확한 흐름

1. C는 현재 `{prefix}/landmarks/+` MQTT 구독과 `/ws/landmarks` WebSocket 수신을 모두 지원한다. Vision이 MQTT로 발행하면 전자를, `GESTURE_WS_URL`을 설정한 기존 Vision 실행은 후자를 사용한다. 두 입력은 같은 adapter/state machine으로 합류한다.
2. `hands`가 비어 있으면 active stroke를 종료한다. `score`는 handedness 점수이므로 threshold로 손을 버리지 않는다.
3. C는 21개 landmark 중 팀이 정한 pointer landmark(예: index fingertip)를 선택한다. camera preview를 mirror했다면 이 시점에만 `x = 1 - x`를 적용하고 이후에는 다시 뒤집지 않는다.
4. C는 One-Euro filter와 outlier/pose 검사를 거쳐 `pointer: {"x": 0..1, "y": 0..1}`를 만든다. Vision 좌표가 frame 밖으로 나갈 수 있으므로 C가 clamp한다. `frame_id`는 B 값을 유지한다.
5. C는 도구 판정 결과를 `DRAW_*` 또는 `ERASE_*`로, 동일 stroke의 ID와 함께 `{prefix}/gestures/{session_id}`에 발행한다.
6. D는 이 입력만 처리해 `CanvasMessage` operation을 `{prefix}/canvas/{session_id}`에 발행한다. D는 Vision의 `hands` payload를 직접 구독하지 않는다.

### 3. C(Gesture): B 좌표를 연속된 그림 명령으로 변환하는 계획

1. C는 B 좌표를 받아 pose 판정과 debounce로 **현재는** `IDLE`, `DRAW_START`, `DRAW_MOVE`, `DRAW_END`만 발행한다. 실제 손등 녹화 748 frame은 이 상태 전이와 WebSocket 입력으로 검증됐다.
2. draw 시작 때 `DRAW_START`, 이동 때 `DRAW_MOVE`, 손실·도구 전환·종료 때 반드시 `DRAW_END`를 보낸다. `stroke_id`는 한 선 전체에 동일해야 한다.
3. 지우개·줌·undo/redo/clear는 Canvas contract에는 준비되어 있으나 Gesture pose/state machine에는 아직 구현되지 않았다. 해당 기능을 추가할 때 `ERASE_START/MOVE/END`, `params.eraser_radius` 등 C contract를 별도 테스트와 함께 확장한다.
4. C는 이미 One-Euro filter를 적용한다. filter를 추가로 강하게 만들기 전에 실제 카메라 FPS에서 지연을 측정한다.
5. C는 이전 좌표와의 거리·시간을 함께 판단한다. 갑자기 멀리 튄 좌표는 outlier로 버리고, 일정 시간 landmark가 없으면 stroke를 종료한다.

### 4. D(Canvas): 점 끊김을 줄이는 현재 보강과 후속 계획

Canvas는 C의 `DRAW_MOVE`/`ERASE_MOVE`에 이미 두 단계 보강을 적용한다.

1. **Low-pass smoothing**: 이전 Canvas 점과 새 점을 `CANVAS_SMOOTHING` 비율로 섞어 landmark 흔들림을 완화한다. 기본 `1.0`(완화 없음)이며, C의 One-Euro filter와 겹치면 지연만 늘어 꺼 두었다.
2. **Linear interpolation**: 두 점 사이 거리가 `CANVAS_SEGMENT_PX`(기본 `8px`)보다 크면 중간 점을 생성한다. renderer는 `stroke_append.points`의 모든 점을 이어 그려야 한다.
3. **고정 논리 Canvas**: 1280×720 좌표계로 환산한 뒤 보간하므로 browser 화면 크기가 달라도 같은 stroke가 나온다.

점이 뚝뚝 끊기는 문제가 남으면 아래 순서로 조정한다.

1. B/C가 `DRAW_MOVE`를 충분한 주기(실측 카메라 FPS 기준)로 발행하는지 먼저 확인한다.
2. renderer가 `points` 배열의 첫 점과 마지막 점만 그리지 않고, 배열 전체를 순서대로 연결하는지 확인한다.
3. `CANVAS_SEGMENT_PX`를 더 작은 값으로 낮춰 중간 점을 늘린다. 너무 작으면 MQTT payload와 CPU가 늘어난다.
4. `CANVAS_SMOOTHING`을 낮춰 더 부드럽게 하되, 사용자가 느끼는 지연을 측정한다.
5. B/C의 outlier 제거와 Canvas 보간을 함께 조정한다. Canvas만 과도하게 smoothing하면 빠른 손 이동이 뒤처진다.

### 단계별 통합 검증 계획

1. C 없이 Canvas UI로 펜/지우개를 그려 D의 MQTT 입력·output·renderer 경로를 검증한다.
2. `mosquitto_pub`로 고정된 `GestureMessage`를 C→D topic에 넣어 schema, session 분리, 보간 점 수를 검증한다.
3. B의 녹화 landmark를 C에 입력해 gesture 전환, 좌표 범위, stroke 종료를 검증한다.
4. A의 실제 카메라에서 A→B→C→D trace_id/frame_id를 로그로 추적하고, 손을 빠르게 움직일 때 끊김·지연·튀는 선을 측정한다.
5. 두 개 session을 동시에 실행해 topic과 화면이 섞이지 않는지 확인한 뒤, reconnect·broker 재시작 시 유실 정책을 확정한다.

## MQTT 계약

Canvas가 구독하는 topic은 `{MQTT_TOPIC_PREFIX}/gestures/+`이며 기본값은 `hgw/v1/gestures/+`다. C는 세션마다 다음 topic으로 `GestureMessage` JSON을 발행해야 한다.

```text
hgw/v1/gestures/{session_id}
```

Canvas는 처리 결과를 다음 topic에 QoS 0, retain false로 JSON 발행한다.

```text
hgw/v1/canvas/{session_id}
```

결과는 `shared/contracts/canvas.schema.json`의 `CanvasMessage`를 따른다. `trace_id`, `session_id`, `frame_id`는 입력에서 바꾸지 않는다.

### C(Gesture)가 보장할 입력 의미

`session_id`는 A부터 D까지 같은 사용자/QR 세션을 식별해야 하며, MQTT topic suffix와 payload 값이 같아야 한다. draw/erase는 `pointer: {x: 0..1, y: 0..1}`와 공통 `params.stroke_id`로 `*_START → *_MOVE... → *_END` 순서를 지킨다. `frame_id`는 세션 내 증가값으로 두고, `trace_id`는 입력 추적에 사용한다.

Canvas는 아래 명령을 받는다.

| 분류 | 명령 | 필수 추가 데이터 |
| --- | --- | --- |
| 펜 | `DRAW_START`, `DRAW_MOVE`, `DRAW_END` | pointer, `params.stroke_id`; 선택 `params.brush_size` |
| 지우개 | `ERASE_START`, `ERASE_MOVE`, `ERASE_END` | pointer, `params.stroke_id`; 선택 `params.eraser_radius` |
| history | `UNDO`, `REDO`, `CLEAR` | pointer 불필요 |
| view | `ZOOM_START`, `ZOOM_UPDATE`, `ZOOM_END` | view parameter는 계약에 맞춰 별도 확인 |

MQTT는 QoS 0, retain false다. 따라서 Canvas는 전송 보장·중복 제거·영속 replay를 제공하지 않는다. C는 발행 전 schema 검증을 하고, reconnect 시 중복/누락 가능성을 고려해 trace/frame/stroke ID를 로그에 남긴다.

### Canvas 출력 소비 규칙

Canvas output에는 session별 단조 증가 `sequence`와 `operations`가 있다. Gateway/renderer는 같은 session의 과거 또는 중복 sequence를 적용하지 않는다. `stroke_append.points`는 보간된 pixel 좌표 전체이므로 순서대로 이어 그린다. erase stroke는 `destination-out` 계열 합성으로 처리한다.

새 browser는 `/ws/board?session_id=<id>`에서 snapshot을 받을 수 있다. 반면 MQTT만 쓰는 consumer를 위한 retained snapshot/replay topic은 아직 없다. 연결 복구 요구가 있으면 output replay 정책을 별도 설계해야 한다.

## 구현된 기능

- Session별 stroke, sequence, view, undo/redo 상태
- `DRAW_START/MOVE/END`, `ERASE_START/MOVE/END`
- `UNDO`, `REDO`, `CLEAR`, `ZOOM_START/UPDATE/END`
- normalized pointer를 1280×720 기본 canvas 좌표로 변환
- 위치 low-pass smoothing과 선형 보간
- HTTP `POST /v1/apply` 및 `GET /v1/sessions/{session_id}/export.png`
- MQTT health endpoint `GET /healthz`
- `http://localhost:8003/?session_id=<id>` 실시간 그림판 UI
- 그림판의 펜·지우개 제어와 독립 굵기 설정(기본 펜 8px, 지우개 30px). UI command도 `/ws/control` → MQTT gesture topic을 거친다.
- session ID별로 stroke 상태와 MQTT topic이 분리된다. URL에 session ID가 없으면 `default` session을 사용한다.
- Canvas 위 포인터는 선택 도구와 굵기를 원형으로 시각화한다.
- `/ws/board` session snapshot과 실시간 CanvasMessage broadcast

## 점 끊김 보강

`DRAW_MOVE`와 `ERASE_MOVE`에서 이전 점과 새 pointer 사이에 두 단계가 적용된다.

1. `CANVAS_SMOOTHING`(기본 `1.0`, 완화 없음)으로 필요할 때만 landmark jitter를 완화한다.
2. `CANVAS_SEGMENT_PX`(기본 `8`)보다 긴 선분은 중간 점으로 분할한다.

예를 들어 80px 이동은 최대 8px 간격의 10개 점으로 canvas operation에 포함된다. Browser renderer는 각 operation의 `points`를 순서대로 연결해야 한다.

## Docker 실행

MQTT broker가 `hgw-net` network에서 `hgw-mqtt:1883`으로 실행 중인 상태를 전제로 한다.

```bash
docker build -t hand-gesture-canvas ./services/canvas
docker run -d --name hgw-canvas --network hgw-net \
  -e MQTT_HOST=hgw-mqtt \
  -e MQTT_PORT=1883 \
  -e MQTT_TOPIC_PREFIX=hgw/v1 \
  hand-gesture-canvas
```

Windows `cmd.exe`에서는 `\` 줄바꿈을 쓰지 말고 README의 한 줄 실행 명령을 사용한다. Canvas가 host에서 열리지 않으면 `-p 8003:8003`을 포함했는지 확인한다. broker는 Canvas container 안에서 `hgw-mqtt:1883`으로 접근하므로 host 공개 포트 `11883`이 Canvas 연결에 쓰이지 않는다.

## 검증 완료

```bash
PYTHONPATH=services/canvas python -m unittest discover -s services/canvas/tests -v
```

- Draw move 보간 점 생성
- Undo/redo operation 생성

## 다음 담당자에게 맡길 작업

아래 순서대로 진행하면 A → B → C → D 통합을 가장 적은 변경으로 검증할 수 있다. **Canvas 담당자가 아닌 서비스의 파일은 이 문서만으로 수정하지 말고, 해당 서비스 담당자와 계약을 확인한 후 변경한다.**

1. **B(Vision) → C(Gesture)의 MQTT 전환을 한 경로로 검증한다.**
   - 최신 Vision은 `hgw/v1/landmarks/{session_id}` 발행을, 최신 Gesture는 `hgw/v1/landmarks/+` 구독을 구현했다.
   - 현재 WebSocket 방식도 유지되므로 한 실행에서는 MQTT 또는 WebSocket 중 하나만 활성화한다. 두 경로의 중복 발행은 금지한다.
   - 748-frame 실제 손등 녹화와 Vision sample을 MQTT 경로에서도 재생해 동일한 `DRAW_*` 결과가 나오는지 확인한다.

2. **C(Gesture)의 지원 범위를 명확히 확장한다.**
   - 현재 C는 schema-valid `GestureMessage`를 QoS 0, retain false로 발행하며 `IDLE`, `DRAW_START/MOVE/END`만 생성한다.
   - pointer는 C에서 0~1로 clamp하고, 같은 stroke는 같은 `stroke_id`로 발행한다.
   - 지우개·색상·굵기·줌·undo/redo/clear는 Canvas는 지원하지만 C의 gesture pose/state machine은 아직 미구현이다. 기능별 command·파라미터·회귀 녹화를 합의한 다음 추가한다.

3. **A(Gateway)가 QR/접속 사용자와 `session_id`를 하나로 고정한다.**
   - A→B→C→D 전체에서 동일 session ID를 전달한다. URL `?session_id=default`는 개발 편의용 기본 session일 뿐, 실제 사용자 식별자로 쓰지 않는다.
   - 다른 사용자의 topic/payload가 섞이지 않도록 session 생성·만료 정책을 정한다.
   - Gateway에서 Canvas output을 화면으로 중계한다면 `{prefix}/canvas/{session_id}`만 구독하고, `sequence`가 과거/중복인 operation은 적용하지 않는다.

4. **통합 화면 또는 renderer를 검증한다.**
   - Canvas 내장 화면은 `/ws/board?session_id=<id>`로 snapshot과 실시간 operation을 이미 제공한다.
   - 별도 renderer는 `stroke_append.points` 전체를 순서대로 연결하고 erase는 `destination-out` 방식으로 구현한다.
   - MQTT 전용 renderer에는 snapshot/replay topic이 아직 없으므로, 재접속 시 WebSocket snapshot을 쓰거나 replay 계약을 추가로 설계한다.

5. **Infra/Platform이 MQTT broker와 배포 구성을 운영용으로 바꾼다.**
   - 별도 승인 후 `compose.yaml`에 broker, Canvas 실행 환경 변수, healthcheck, restart policy를 추가한다.
   - 개발용 `mosquitto-no-auth.conf` 대신 TLS·계정·ACL을 설정한다. 외부 tunnel에는 broker 포트 `1883/11883`을 노출하지 않는다.
   - ACL은 C/제어 UI의 gesture publish와 Gateway/renderer의 canvas subscribe를 session 단위 최소 권한으로 제한한다.

6. **Canvas 후속 개선의 우선순위를 결정한다.**
   - 현재 Canvas 상태는 container 메모리이므로 재시작하면 그림이 사라진다. 지속성이 필요하면 Redis/DB와 session affinity를 설계한다.
   - malformed payload, 처리 지연, MQTT reconnect를 metrics/log로 수집한다.
   - `REDO`는 engine이 `restore_stroke` operation을 내보내지만 현재 내장 browser renderer는 이를 복원하지 않는다. UI에서 redo를 제공할 경우 stroke replay 정보와 renderer 처리를 함께 보완한다.

## 다른 서비스 담당자에게 반드시 전달할 사항

1. **Canvas는 JPEG, QR 영상, raw hand landmark를 받지 않는다.** C가 gesture 명령과 normalized pointer로 변환한 뒤 MQTT로 보내야 한다.
2. **고정 Canvas 좌표는 1280×720이다.** `pointer`는 화면 pixel이 아닌 0~1 값이며 Canvas가 pixel로 환산한다. B/C가 browser 크기를 기준으로 px 값을 그대로 보내면 선 위치가 어긋난다.
3. **입력/출력 topic은 아래 두 개다.** prefix 기본값은 `hgw/v1`이며 session suffix를 임의로 바꾸면 안 된다.

   ```text
   C → D: hgw/v1/gestures/{session_id}
   D → Gateway/renderer: hgw/v1/canvas/{session_id}
   ```

4. **QoS 0, retain false는 유실 가능성을 뜻한다.** Canvas는 중복 제거·메시지 저장·재전송을 보장하지 않는다. C/Gateway는 `trace_id`, `frame_id`, `stroke_id`, output `sequence`를 로그와 중복 방지에 사용한다.
5. **펜 설정은 `params.color`(`#RRGGBB`)와 `params.brush_size`(px)다.** 지우개는 `params.eraser_radius`를 사용하며, 원하는 px 폭을 맞추려면 `폭_px / (2 × min(CANVAS_WIDTH, CANVAS_HEIGHT))`로 계산한다.
6. **같은 session ID의 browser는 그림을 공유한다.** URL에 session ID가 없으면 `default`가 사용된다. 테스트는 `?session_id=team-a`처럼 별도 ID를 사용해 서로 섞이지 않는지 확인한다.
7. **Canvas 재시작은 현재 그림을 지운다.** `/ws/board` snapshot은 살아 있는 Canvas process의 메모리 상태만 복원한다.

## 후속 개발 범위와 결정 필요 사항

| 우선순위 | 담당 | 작업 | Canvas 변경 필요 여부 |
| --- | --- | --- | --- |
| 높음 | B/C | 실제 구현된 `hgw/v1/landmarks/+` MQTT 경로를 WebSocket 단독 경로와 중복 없이 선택·E2E 검증 | 현재 Canvas 변경 없음 |
| 높음 | A/Gateway | QR 또는 접속 사용자를 고유 `session_id`로 만들고 A→B→C→D에서 유지 | 현재 Canvas 변경 없음 |
| 높음 | B/C/Contract | capture `ts`를 Canvas output까지 보존할지 결정. 필요 시 공통 contract에 `captured_at_ms` 추가 | 공통 contract 변경 승인 필요 |
| 중간 | C Gesture | 지우개·색상·굵기 및 기타 Canvas command pose/state machine 구현 | Canvas contract는 이미 준비됨 |
| 높음 | Infra | 인증 없는 개발용 broker를 TLS·계정·ACL broker로 교체 | 설정/배포 변경 필요 |
| 중간 | Gateway/renderer | `{prefix}/canvas/{session}` 구독, sequence 처리, reconnect snapshot 전략 구현 | Canvas MQTT snapshot 요구 시 확장 필요 |
| 중간 | Canvas | invalid payload metrics, 처리 지연 metrics, MQTT replay/snapshot 지원 | Canvas 후속 개발 |
| 중간 | Platform | Compose healthcheck, restart policy, 로그/모니터링, secret 관리 | Canvas 외부 작업 |
| 낮음 | Canvas/Platform | Redis/DB 기반 session state, replica session affinity | Canvas 설계 확장 필요 |

### 운영 보안과 안정성

- `mosquitto-no-auth.conf`는 개발 전용이다. 외부 tunnel 또는 공용 네트워크에서 broker 포트 `1883/11883`을 공개하지 않는다.
- ACL은 C 또는 제어 UI의 `{prefix}/gestures/{허용-session}` publish와 renderer/Gateway의 `{prefix}/canvas/{허용-session}` subscribe를 최소 권한으로 분리한다.
- 현재 payload 상한은 256KB이며 malformed JSON/계약 위반 메시지는 처리하지 않는다. 관측성을 위해 C와 broker 측에서 error log/metric을 추가한다.
- Canvas container 재시작 시 메모리 안의 session drawing이 삭제된다. 지속성 요구가 확정되기 전에는 재시작을 그림 유실 이벤트로 취급한다.

### 통합 인수 기준

1. C가 실제 B landmark 흐름을 `GestureMessage`로 변환하고 session별 입력 topic으로 발행한다.
2. Canvas `/healthz`가 `mqtt_connected: true`이고, output topic에 같은 `trace_id/session_id/frame_id`가 나타난다.
3. 서로 다른 두 browser 또는 Gateway renderer가 같은 session에서 선·지우개·undo 결과를 동일하게 보인다.
4. 다른 session의 topic/payload는 화면과 상태에 섞이지 않는다.
5. broker/Canvas 재시작과 browser reconnect 때의 상태 손실·복구 정책을 팀이 문서로 확정한다.

## UI 굵기 동작 규칙

펜과 지우개는 서로 다른 굵기 값을 보존한다. UI는 펜 stroke에 `params.brush_size`와 `params.color`(`#RRGGBB`)를, 지우개 stroke에 `params.eraser_radius`를 normalized radius로 전송한다. 펜 색상은 팔레트·색상 맵·HEX 입력으로 설정되며 세 UI는 동일한 유효 HEX 값을 유지한다. Canvas는 지우개 radius를 고정 canvas 기준 px 폭으로 환산한다. 따라서 C가 같은 느낌의 지우개를 구현하려면 `eraser_radius = 원하는_폭_px / (2 × min(CANVAS_WIDTH, CANVAS_HEIGHT))` 공식을 사용한다. 지우개 `ERASE_START`만으로도 단일 원형 erase operation이 생성되므로, 클릭 한 번은 그 영역을 즉시 지우며 드래그는 이어지는 `ERASE_MOVE`로 확장된다. 현재 UI의 반투명 점선 원은 선택한 도구의 실제 적용 폭을 표시한다.
