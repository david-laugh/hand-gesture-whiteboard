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

## 구현된 기능

- Session별 stroke, sequence, view, undo/redo 상태
- `DRAW_START/MOVE/END`, `ERASE_START/MOVE/END`
- `UNDO`, `REDO`, `CLEAR`, `ZOOM_START/UPDATE/END`
- normalized pointer를 1280×720 기본 canvas 좌표로 변환
- 위치 low-pass smoothing과 선형 보간
- HTTP `POST /v1/apply` 및 `GET /v1/sessions/{session_id}/export.png`
- MQTT health endpoint `GET /healthz`

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

## 검증 완료

```bash
PYTHONPATH=services/canvas python -m unittest discover -s services/canvas/tests -v
```

- Draw move 보간 점 생성
- Undo/redo operation 생성

## 다음 담당자가 연결할 부분

1. Gesture 서비스가 `hgw/v1/gestures/{session_id}`에 schema-valid `GestureMessage`를 발행한다.
2. Gateway 또는 browser renderer가 `hgw/v1/canvas/{session_id}`를 구독하고 `CanvasMessage.operations`를 렌더링한다.
3. 전체 MQTT 구성은 별도 승인 후 `compose.yaml`에 broker, Canvas command, topic 환경 변수를 추가한다.
4. 다중 Canvas replica가 필요하면 session affinity나 외부 session store가 필요하다. 현재 상태는 container 메모리에 있다.
