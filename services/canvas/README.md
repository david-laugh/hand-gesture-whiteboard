# Canvas (Container D)

Canvas는 Gesture가 만든 명령을 MQTT로 받아 그림 상태를 관리하고, browser renderer가 적용할 `CanvasMessage.operations`를 MQTT로 발행합니다. 카메라 JPEG나 hand landmark는 이 서비스에 전달하지 않습니다.

```text
Gesture command ── MQTT ──► Canvas ── MQTT ──► canvas operations
 hgw/v1/gestures/{session}             hgw/v1/canvas/{session}
```

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

그림판의 **펜**, **지우개**, **굵기** 컨트롤도 사용할 수 있습니다. 마우스/터치가 canvas 위에 있으면 선택한 굵기와 같은 크기의 원형 포인터가 보입니다. 이 UI에서 직접 그리면 browser는 `/ws/control`으로 gesture를 보내고, Canvas는 이를 `{prefix}/gestures/{session_id}` MQTT topic에 발행합니다. Canvas가 같은 topic을 구독해 처리한 뒤 `{prefix}/canvas/{session_id}` 결과를 다시 그림판에 전달하므로, UI 테스트와 실제 C(Gesture) 입력은 같은 MQTT 처리 경로를 사용합니다.

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
