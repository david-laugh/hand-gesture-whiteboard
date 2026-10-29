# Canvas 구현 인수인계

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

이 절은 전체 팀이 합의해야 할 **계획**이다. Canvas(D)의 현재 입력/출력 topic만 구현되어 있으며, A→B와 B→C topic은 해당 서비스 담당자가 contract를 확정한 후 구현한다. Canvas를 C로 부르지 않고 역할을 명확히 구분한다: **C는 Gesture command producer, D는 Canvas consumer/operation producer**다.

| 순서 | 발행 서비스 → 구독 서비스 | 제안 topic | 전달 데이터 | 반드시 보장할 것 |
| --- | --- | --- | --- | --- |
| 1 | A Gateway → B Vision | `{prefix}/frames/{session_id}` | camera frame 참조 또는 JPEG, frame_id, timestamp_ms, trace_id | 같은 세션·프레임 식별자 전달, 대용량 frame 정책 합의 |
| 2 | B Vision → C Gesture | `{prefix}/landmarks/{session_id}` | hand landmark 좌표, handedness, confidence, frame_id, timestamp_ms, trace_id | 좌표계·손 선택·낮은 confidence 처리 기준 합의 |
| 3 | C Gesture → D Canvas | `{prefix}/gestures/{session_id}` | `GestureMessage`: gesture, normalized pointer, stroke_id, style | START/MOVE/END 순서, 0~1 pointer, session 일치 |
| 4 | D Canvas → A/renderer | `{prefix}/canvas/{session_id}` | `CanvasMessage`: sequence, operations, history | operation 순서 적용, reconnect 복구 정책 |

`{prefix}` 기본값은 `hgw/v1`이다. 1·2번 topic은 팀 제안이며 아직 Canvas 코드나 공통 contract에 고정하지 않았다. 이를 확정할 때도 `session_id`, `frame_id`, `timestamp_ms`, `trace_id`의 이름과 의미를 A부터 D까지 동일하게 유지한다.

### 1. A(Gateway) → B(Vision): 카메라 입력 계획

1. A는 QR 또는 연결 사용자마다 고유 `session_id`를 만든다. `default`는 로컬 테스트 전용이다.
2. A는 각 frame에 증가하는 `frame_id`, 캡처 시각 `timestamp_ms`, 추적용 `trace_id`를 넣는다.
3. JPEG를 MQTT payload에 직접 실을지, object URL/HTTP endpoint 참조만 실을지 먼저 결정한다. MQTT payload 상한과 broker 부하 때문에 고해상도 영상은 URL/스트림 참조 방식을 우선 검토한다.
4. B는 최신 frame만 처리하는 drop policy를 둔다. 늦게 도착한 오래된 frame을 모두 처리하면 손 움직임과 그림이 지연된다.

### 2. B(Vision) → C(Gesture): 좌표 계약과 보강 계획

1. B는 선택한 손가락 tip의 좌표, 가능하면 손 전체 landmark, handedness, confidence를 C로 보낸다.
2. 좌표계는 **이미지 폭/높이 기준 normalized 0~1**로 통일한다. C는 mirror된 사용자 미리보기인지 원본 카메라인지 한 번만 변환하고, 같은 축을 D까지 유지한다.
3. B는 confidence가 낮거나 landmark가 없을 때 `pointer`를 억지로 이전 값으로 반복 발행하지 않는다. `hand_present: false` 또는 명확한 상태를 보내고 C가 현재 stroke를 안전하게 종료한다.
4. C는 B의 `frame_id`와 timestamp를 보존해 지연·누락을 관측한다. 과거 frame은 버리고 새 frame을 우선하는 정책을 사용한다.

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
4. Vision payload의 `ts`는 Canvas `GestureMessage.timestamp_ms`에 그대로 매핑한다. Vision에는 `trace_id`가 없으므로 C가 session별 또는 frame별 고유 trace ID를 생성해 넣는다. `message_type`은 C output에서 `"gesture"`, `schema_version`은 `"1.0.0"`으로 채운다.
5. `gesture`는 Vision 샘플에서 `null`이다. 현 단계에서는 C가 landmark를 직접 판정해 draw/erase/idle을 만들며, 이후 B가 gesture hint를 제공해도 C가 최종 Canvas 명령을 결정한다.

### B payload를 C의 Canvas 입력으로 바꾸는 정확한 흐름

1. C는 `{prefix}/landmarks/{session_id}`를 구독한다. 이 topic은 위 Vision 샘플을 반영한 **제안**이며 B/C 담당자가 확정해야 한다.
2. `hands`가 비어 있거나 선택한 hand의 `score`가 threshold 미만이면 active stroke를 끝내고 해당 frame은 pointer로 발행하지 않는다.
3. C는 21개 landmark 중 팀이 정한 pointer landmark(예: index fingertip)를 선택한다. camera preview를 mirror했다면 이 시점에만 `x = 1 - x`를 적용하고 이후에는 다시 뒤집지 않는다.
4. C는 EMA/median filter와 outlier 검사 후 `pointer: {"x": 0..1, "y": 0..1}`를 만든다. `frame_id`는 B 값을 유지하고 `timestamp_ms`에는 `ts`를 넣는다.
5. C는 도구 판정 결과를 `DRAW_*` 또는 `ERASE_*`로, 동일 stroke의 ID와 함께 `{prefix}/gestures/{session_id}`에 발행한다.
6. D는 이 입력만 처리해 `CanvasMessage` operation을 `{prefix}/canvas/{session_id}`에 발행한다. D는 Vision의 `hands` payload를 직접 구독하지 않는다.

### 3. C(Gesture): B 좌표를 연속된 그림 명령으로 변환하는 계획

1. C는 B 좌표를 받아 dead zone, confidence threshold, 손가락 gesture 판정으로 펜/지우개/중지를 결정한다.
2. draw 시작 때 `DRAW_START`, 이동 때 `DRAW_MOVE`, 손실·도구 전환·종료 때 반드시 `DRAW_END`를 보낸다. `stroke_id`는 한 선 전체에 동일해야 한다.
3. 지우개는 `ERASE_START/MOVE/END`를 사용한다. 펜은 `params.color`(`#RRGGBB`), `params.brush_size`(px), 지우개는 `params.eraser_radius`를 함께 보낸다.
4. C의 첫 보강 단계는 exponential moving average(EMA) 또는 median filter로 landmark jitter를 줄이는 것이다. 너무 강한 filter는 그림이 손보다 늦게 따라오므로 실제 카메라 FPS에서 조정한다.
5. C는 이전 좌표와의 거리·시간을 함께 판단한다. 갑자기 멀리 튄 좌표는 outlier로 버리고, 일정 시간 landmark가 없으면 stroke를 종료한다.

### 4. D(Canvas): 점 끊김을 줄이는 현재 보강과 후속 계획

Canvas는 C의 `DRAW_MOVE`/`ERASE_MOVE`에 이미 두 단계 보강을 적용한다.

1. **Low-pass smoothing**: 이전 Canvas 점과 새 점을 `CANVAS_SMOOTHING`(기본 `0.55`) 비율로 섞어 landmark 흔들림을 완화한다.
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

1. `CANVAS_SMOOTHING`(기본 `0.55`)으로 landmark jitter를 완화한다.
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

1. **B(Vision) → C(Gesture) 입력 계약을 확정한다.**
   - B는 session별 hand landmark, handedness, confidence, frame timestamp를 C가 읽을 수 있는 형식으로 전달한다.
   - B→C의 MQTT topic 또는 IPC 방식은 아직 정해지지 않았다. Canvas topic을 재사용하지 않는다.
   - C가 jitter를 줄일 filtering 주기와 gesture 판정 임계값을 이 단계에서 합의한다.

2. **C(Gesture)에 Canvas용 command publisher를 구현한다.**
   - B의 landmark를 `DRAW_*`, `ERASE_*`, `UNDO`, `REDO`, `CLEAR`로 판정한다.
   - session별 `hgw/v1/gestures/{session_id}`에 schema-valid `GestureMessage`를 QoS 0, retain false로 발행한다.
   - `pointer.x/y`는 반드시 0~1 normalized 값, topic suffix와 payload `session_id`는 반드시 같은 값으로 만든다.
   - 한 선은 같은 `stroke_id`를 이용해 `*_START → *_MOVE... → *_END`로 발행하고, `frame_id`는 session 안에서 증가시킨다.

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
| 높음 | B/C | B landmark 출력 형식과 B→C MQTT/IPC topic을 정하고 C가 gesture 명령을 생성 | 현재 Canvas 변경 없음 |
| 높음 | A/Gateway | QR 또는 접속 사용자를 고유 `session_id`로 만들고 A→B→C→D에서 유지 | 현재 Canvas 변경 없음 |
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
