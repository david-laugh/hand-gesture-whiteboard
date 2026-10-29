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

## 다음 담당자가 연결할 부분

1. Gesture 서비스가 `hgw/v1/gestures/{session_id}`에 schema-valid `GestureMessage`를 발행한다.
2. Gateway 또는 browser renderer가 `hgw/v1/canvas/{session_id}`를 구독하고 `CanvasMessage.operations`를 렌더링한다.
   Canvas 자체 그림판은 이미 `/ws/board`로 동일 operation을 렌더링하므로, C 연동 전에도 펜/지우개 UI로 MQTT 경로를 시험할 수 있다.
3. 전체 MQTT 구성은 별도 승인 후 `compose.yaml`에 broker, Canvas command, topic 환경 변수를 추가한다.
4. 다중 Canvas replica가 필요하면 session affinity나 외부 session store가 필요하다. 현재 상태는 container 메모리에 있다.

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
