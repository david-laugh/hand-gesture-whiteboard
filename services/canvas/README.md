# Canvas (Container D)

Canvas는 Gesture가 만든 명령을 MQTT로 받아 그림 상태를 관리하고, browser renderer가 적용할 `CanvasMessage.operations`를 MQTT로 발행합니다. 카메라 JPEG나 hand landmark는 이 서비스에 전달하지 않습니다.

```text
Gesture command ── MQTT ──► Canvas ── MQTT ──► canvas operations
 hgw/v1/gestures/{session}             hgw/v1/canvas/{session}
```

## 전체 파이프라인에서의 위치와 연결 상태

Canvas는 A(Gateway) → B(Vision) → C(Gesture) → D(Canvas) 흐름의 마지막 단계입니다. 현재 Canvas에 구현되어 검증된 입력은 **C가 만든 GestureMessage**뿐입니다. 따라서 B가 만든 landmark, A가 받은 카메라 프레임, QR 영상은 Canvas로 직접 보내면 안 됩니다.

| 구간 | 책임 | Canvas 기준 상태 |
| --- | --- | --- |
| A → B | Gateway가 세션별 카메라 프레임을 전달 | Canvas 범위 밖 |
| B → C | Vision이 landmark·confidence를 전달 | topic/계약은 별도 서비스에서 결정 필요 |
| C → D | Gesture가 landmark를 draw/erase 명령과 normalized pointer로 변환해 MQTT 발행 | Canvas가 수신하도록 구현됨 |
| D → 화면/Gateway | Canvas operation을 MQTT 또는 `/ws/board`로 전달 | Canvas 발행 및 내장 renderer 구현됨 |

다음 단계에서 C 담당자는 B의 landmark 스트림을 받아 `DRAW_*`, `ERASE_*`, `UNDO`, `REDO`, `CLEAR` 중 하나로 판정하고 아래 MQTT 입력 계약대로 발행해야 합니다. B→C topic 이름은 이 저장소에서 아직 고정하거나 변경하지 않았습니다.

## Docker로 실행

### 1. MQTT network와 broker 준비

Gateway에서 이미 `hgw-net`과 `hgw-mqtt`를 실행했다면 이 단계는 건너뜁니다. 처음 한 번만 실행합니다.

```bash
docker network create hgw-net
docker run -d --name hgw-mqtt --network hgw-net -p 127.0.0.1:11883:1883 eclipse-mosquitto:2 mosquitto -c /mosquitto-no-auth.conf
```

Windows `cmd.exe`에서는 `\`로 줄을 나누지 않습니다. 위 명령을 각각 한 줄로 붙여넣습니다. `network with name hgw-net already exists`는 network가 이미 준비되었다는 뜻이므로 무시합니다. `hgw-mqtt`가 이미 실행 중인지 확인하려면 `docker ps --filter name=hgw-mqtt`를 실행합니다.

`hgw-net`은 container가 `hgw-mqtt`라는 이름으로 broker에 연결하도록 하는 Docker network입니다. `11883` host 포트는 PC에서 MQTT 확인 도구를 쓸 때만 사용하며, Canvas container는 내부 포트 `1883`으로 연결합니다.

### 2. Canvas 이미지 build

repository root에서 실행합니다.

```bash
docker build -t hand-gesture-canvas ./services/canvas
```

### 3. Canvas container 실행

```bash
docker run -d --name hgw-canvas --network hgw-net -p 8003:8003 -e MQTT_HOST=hgw-mqtt -e MQTT_PORT=1883 -e MQTT_TOPIC_PREFIX=hgw/v1 -e CANVAS_WIDTH=1280 -e CANVAS_HEIGHT=720 hand-gesture-canvas
```

`http://localhost:8003/?session_id=<session_id>`에서 실시간 그림판을 엽니다. 이 페이지는 Canvas가 MQTT로 받은 operation을 WebSocket으로 전달받아 그립니다. Canvas는 고정된 `1280×720` 논리 좌표를 사용하며 브라우저 크기가 바뀌어도 stroke 좌표는 바뀌지 않습니다.

`session_id`는 하나의 독립된 그림 상태를 구분하는 이름입니다. URL에 없으면 `default`를 사용하므로 `http://localhost:8003/`와 `http://localhost:8003/?session_id=default`는 같은 그림판입니다. `?session_id=team-a`처럼 다른 값을 쓰면 MQTT topic, stroke history, 화면 snapshot이 `team-a` 전용으로 분리됩니다. 같은 ID를 연 여러 브라우저는 동일한 그림을 실시간으로 봅니다.

그림판은 **펜 굵기**와 **지우개 굵기**를 각각 독립적으로 기억합니다. 기본값은 펜 8px, 지우개 30px이며 각 슬라이더를 조절해도 다른 도구의 값은 바뀌지 않습니다. 펜 색상은 기본 8색 팔레트, 브라우저 색상 맵, `#RRGGBB` HEX 직접 입력으로 지정할 수 있고 세 입력은 동기화됩니다. 잘못된 HEX는 적용하지 않고 이전 유효 색상을 유지합니다. 마우스/터치가 canvas 위에 있으면 현재 선택한 도구의 굵기와 같은 크기의 원형 포인터가 보입니다. 지우개는 반투명 채움·점선 테두리·내부 원으로 현재 지워질 영역을 표시하며, 이 원의 지름이 실제 erase operation 폭과 같습니다. 지우개로 **한 번 클릭**하면 그 원형 영역을 즉시 지우고, 드래그하면 같은 폭으로 연속해서 지웁니다. 이 UI에서 직접 그리면 browser는 `/ws/control`으로 gesture를 보내고, Canvas는 이를 `{prefix}/gestures/{session_id}` MQTT topic에 발행합니다. Canvas가 같은 topic을 구독해 처리한 뒤 `{prefix}/canvas/{session_id}` 결과를 다시 그림판에 전달하므로, UI 테스트와 실제 C(Gesture) 입력은 같은 MQTT 처리 경로를 사용합니다.

### 4. 실행 상태 확인

```bash
docker exec hgw-canvas python -c "import urllib.request; print(urllib.request.urlopen('http://localhost:8003/healthz').read().decode())"
docker logs hgw-canvas
```

정상 연결 예시입니다.

```json
{"status":"ok","service":"canvas","mqtt_connected":true}
```

`mqtt_connected`가 `false`이면 `hgw-mqtt` container가 실행 중인지, Canvas와 broker가 모두 `hgw-net`에 속하는지 확인합니다.

## MQTT 통신 규격

| 방향 | Topic | Payload | QoS / retain |
| --- | --- | --- | --- |
| Gesture → Canvas | `{prefix}/gestures/{session_id}` | `GestureMessage` JSON | 0 / false |
| Canvas → Renderer | `{prefix}/canvas/{session_id}` | `CanvasMessage` JSON | 0 / false |

기본 `prefix`는 `hgw/v1`입니다. Canvas는 `{prefix}/gestures/+`를 구독하고, 받은 `session_id`와 동일한 suffix로 결과를 발행합니다.

### 입력 계약: GestureMessage

| 필드 | 필수 | 규칙 |
| --- | --- | --- |
| `schema_version` | 예 | 현재 `"1.0.0"` |
| `message_type` | 예 | `"gesture"` |
| `trace_id` | 예 | 한 입력 흐름을 추적할 고유 ID. Canvas output에도 그대로 전달 |
| `session_id` | 예 | MQTT topic suffix와 반드시 동일 |
| `frame_id` | 예 | C가 세션 안에서 증가시키는 프레임/명령 번호 |
| `timestamp_ms` | 예 | 발생 시각(ms) |
| `gesture` | 예 | 아래 명령 중 하나 |
| `pointer` | draw/erase에 예 | normalized `{x: 0..1, y: 0..1}` |
| `confidence` | 예 | C의 판정 confidence |
| `params.stroke_id` | stroke 명령에 예 | START/MOVE/END가 공유하는 stroke ID |

지원 명령은 `DRAW_START`, `DRAW_MOVE`, `DRAW_END`, `ERASE_START`, `ERASE_MOVE`, `ERASE_END`, `UNDO`, `REDO`, `CLEAR`, `ZOOM_START`, `ZOOM_UPDATE`, `ZOOM_END`입니다. 펜 굵기는 `params.brush_size`(px), 지우개 폭은 `params.eraser_radius`(normalized radius)로 지정할 수 있습니다. 포인터가 필요한 draw/erase 명령에는 `pointer`를 생략하지 않습니다.

한 stroke는 같은 `stroke_id`로 `*_START → *_MOVE... → *_END` 순서로 보내야 합니다. Canvas는 받은 순서에 따라 stroke를 유지하며, 현재 broker 설정(QoS 0)에서는 전송 재시도나 정확히 한 번 처리 보장을 제공하지 않습니다.

### Gesture 입력 예시

아래 예시는 `session-demo`에 검은 stroke를 시작합니다. PC host에서 broker의 `11883` 포트로 보냅니다.

```bash
docker run --rm --network hgw-net eclipse-mosquitto:2 \
  mosquitto_pub -h hgw-mqtt -p 1883 \
  -t hgw/v1/gestures/session-demo \
  -m '{"schema_version":"1.0.0","message_type":"gesture","trace_id":"demo-trace-001","session_id":"session-demo","frame_id":0,"timestamp_ms":0,"gesture":"DRAW_START","pointer":{"x":0.2,"y":0.3},"confidence":1,"params":{"stroke_id":"stroke-1"},"gesture_ms":0}'
```

다른 terminal에서 결과를 구독합니다.

```bash
docker run --rm --network hgw-net eclipse-mosquitto:2 \
  mosquitto_sub -h hgw-mqtt -p 1883 \
  -t hgw/v1/canvas/session-demo -v
```

출력에는 `message_type: "canvas"`, `sequence`, `operations`가 포함됩니다. `DRAW_MOVE`에 대한 operation에는 보간된 여러 `points`가 들어갈 수 있습니다.

### 출력 계약: CanvasMessage와 renderer 규칙

Canvas는 각 session에서 증가하는 `sequence`와 `operations` 배열을 보냅니다. Gateway 또는 별도 renderer는 동일 session 안에서 `sequence`를 기준으로 중복·과거 메시지를 무시하고, 연결을 다시 맺었을 때 `/ws/board` snapshot 또는 별도 재동기화 절차를 사용해야 합니다. MQTT output만 구독하는 새 renderer에 대한 snapshot topic은 아직 구현되지 않았습니다.

`operations`에는 `stroke_begin`, `stroke_append`, `stroke_end`, `undo`, `redo`, `clear`, `zoom`이 포함될 수 있습니다. `stroke_append.points`는 보간된 pixel 좌표 목록입니다. renderer는 목록 안의 점을 순서대로 연결해야 선이 끊기지 않습니다. erase stroke는 합성 모드 `destination-out`처럼 픽셀을 지우는 방식으로 적용해야 합니다.

### Topic, 순서, 재전송 정책

- topic wildcard 구독은 Canvas 내부의 `{prefix}/gestures/+`로만 제한합니다. C는 다른 session의 topic에 발행하지 않습니다.
- Canvas는 256KB보다 큰 payload와 JSON이 아닌 payload를 처리하지 않습니다. 잘못된 메시지는 현재 조용히 폐기되므로, C는 발행 전 schema 검증과 로그를 남겨야 합니다.
- 모든 Canvas MQTT 메시지는 QoS 0, retain false입니다. broker 재시작·네트워크 단절 시 메시지가 유실될 수 있고, retain된 과거 gesture는 Canvas가 처리하지 않습니다.
- MQTT는 같은 topic의 발행 순서를 보통 유지하지만, C의 재연결·중복 발행·여러 publisher 상황까지 Canvas가 deduplicate하지는 않습니다. `frame_id`, `trace_id`, `stroke_id`를 로그와 재전송 판단에 사용합니다.

## Drawing과 점 보간

Canvas는 C가 보낸 normalized `pointer` `{x: 0..1, y: 0..1}`를 고정된 `1280×720` pixel 좌표로 바꿉니다. `DRAW_START/MOVE/END`는 펜, `ERASE_START/MOVE/END`는 지우개입니다. `DRAW_MOVE`와 `ERASE_MOVE`에는 두 단계가 적용됩니다.

1. Low-pass smoothing: 이전 점과 새 좌표 사이를 `CANVAS_SMOOTHING` 비율로 완화합니다.
2. Linear interpolation: 두 점이 `CANVAS_SEGMENT_PX`보다 멀면 중간 점을 생성합니다.

기본 설정에서는 80px 이동을 최대 8px 간격의 연속된 점들로 나누므로, landmark가 일정하지 않아도 선이 뚝뚝 끊겨 보이는 현상을 줄입니다.

## 그림판 WebSocket

| Path | 역할 |
| --- | --- |
| `/ws/board?session_id=<id>` | Canvas operation과 현재 stroke snapshot을 browser에 전달 |
| `/ws/control?session_id=<id>` | 펜·지우개 UI gesture를 받아 MQTT gesture topic으로 발행 |

`/ws/board`는 새 browser가 접속해도 기존 session의 visible stroke를 snapshot으로 보내므로, 화면을 새로 열어도 현재 그림을 볼 수 있습니다.

`/ws/control`은 개발·현장 점검을 위한 UI 입력 경로입니다. 펜은 `brush_size`와 `color`(`#RRGGBB`), 지우개는 `eraser_radius`로 각자 선택한 설정을 동일한 MQTT gesture topic에 발행하므로 C 연동 전에도 D의 MQTT 처리 경로를 확인할 수 있습니다. 운영 클라이언트가 직접 gesture를 발행할 권한을 가질지는 broker ACL로 제한해야 합니다.

## HTTP endpoint

이 endpoint들은 Docker network 내부에서 디버그·export 용도로 사용할 수 있습니다.

| Method | Path | 설명 |
| --- | --- | --- |
| `GET` | `/healthz` | MQTT 연결을 포함한 상태 |
| `POST` | `/v1/apply` | `GestureMessage` JSON을 직접 적용하고 `CanvasMessage` 반환 |
| `GET` | `/v1/sessions/{session_id}/export.png` | 현재 session의 PNG export |

## 환경 변수

| 변수 | 기본값 | 설명 |
| --- | --- | --- |
| `MQTT_HOST` | `localhost` | MQTT broker host. Docker에서는 `hgw-mqtt` |
| `MQTT_PORT` | `11883` | broker port. Docker에서는 `1883` |
| `MQTT_TOPIC_PREFIX` | `hgw/v1` | MQTT topic prefix |
| `CANVAS_WIDTH` | `1280` | output canvas 폭(px) |
| `CANVAS_HEIGHT` | `720` | output canvas 높이(px) |
| `CANVAS_SMOOTHING` | `0.55` | 위치 완화 비율. 작을수록 더 부드럽지만 지연이 늘어남 |
| `CANVAS_SEGMENT_PX` | `8` | 보간 점 최대 간격(px) |

## 운영 전 필요한 MQTT 사항

개발용 broker는 `mosquitto-no-auth.conf`를 사용하므로 인증이 없습니다. 인터넷에 노출하지 말고 운영 환경에서는 최소한 아래를 적용합니다.

- TLS, 계정 인증, ACL을 활성화한다.
- C/제어 UI에는 `{prefix}/gestures/{자기-session}` publish만, renderer/Gateway에는 `{prefix}/canvas/{자기-session}` subscribe만 허용한다.
- broker와 Canvas의 연결 상태, 초당 입력 수, 폐기된 잘못된 payload 수, 처리 지연을 수집한다. 현재 `/healthz`는 MQTT 연결 여부만 제공한다.
- Canvas 상태는 container 메모리다. container 재시작 시 stroke history가 사라진다. 다중 replica나 재시작 복구가 필요하면 session affinity와 Redis/DB 같은 외부 상태 저장소를 설계한다.
- Canvas 출력은 QoS 0이므로, 화면 복구는 `/ws/board` snapshot을 사용하거나 MQTT용 snapshot/replay protocol을 별도 설계한다.

## 다음 개발 체크리스트

| 담당 영역 | 필요한 작업 | 현재 상태 |
| --- | --- | --- |
| B Vision | frame별 landmark와 confidence를 C가 소비할 계약으로 발행 | Canvas에서 미구현·미결정 |
| C Gesture | landmark filtering, gesture 판정, `GestureMessage` schema 검증, session별 MQTT publish | Canvas 입력 대기 |
| A Gateway | 사용자/QR session을 A→B→C→D의 같은 `session_id`에 고정하고 Canvas output을 화면으로 중계 | Canvas 외부 작업, 별도 승인 필요 |
| Broker/Compose | broker 설정, 서비스 환경 변수, healthcheck, 재시작 정책을 전체 compose에 추가 | Canvas 외부 작업, 별도 승인 필요 |
| Canvas | invalid-message metrics, MQTT renderer용 snapshot/replay, persistent state, 접근 제어 | 후속 개선 후보 |

통합 완료 기준은 C가 실제 카메라 gesture를 입력 topic에 발행하고, 다른 browser 또는 Gateway renderer가 같은 `session_id`의 output을 받아 연속된 선과 지우개 결과를 재현하는 것입니다.

## 문제 해결

| 증상 | 확인할 항목 |
| --- | --- |
| `mqtt_connected: false` | `hgw-mqtt` 실행 여부, 두 container의 `hgw-net` 연결, Canvas에는 `MQTT_HOST=hgw-mqtt`, `MQTT_PORT=1883`인지 확인 |
| 화면은 열리지만 C 입력이 안 보임 | C topic이 `hgw/v1/gestures/{session_id}`인지, payload의 `session_id`가 URL의 값과 같은지, `pointer`가 0~1인지 확인 |
| 선이 끊기거나 늦음 | C 발행 주기, `CANVAS_SMOOTHING`, `CANVAS_SEGMENT_PX`와 renderer가 `points` 전체를 연결하는지 확인 |
| 새로고침 후 그림이 없음 | 같은 `session_id`인지 확인. Canvas container를 재시작했다면 메모리 상태가 초기화된 것 |
| 다른 사용자의 그림이 섞임 | session ID가 재사용되었거나 Gateway의 session mapping/ACL이 없는 상태. session 생성과 broker ACL을 먼저 설계 |

## 테스트와 종료

```bash
PYTHONPATH=services/canvas python -m unittest discover -s services/canvas/tests -v
docker stop hgw-canvas
docker rm hgw-canvas
```

Broker까지 종료할 때만 다음을 실행합니다. Gateway 등 다른 service가 broker를 쓰고 있으면 종료하지 않습니다.

```bash
docker stop hgw-mqtt
docker rm hgw-mqtt
```
