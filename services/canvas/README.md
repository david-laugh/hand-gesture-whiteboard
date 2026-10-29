# canvas (Container D)

Canvas는 gesture 명령을 MQTT로 받아 stroke 상태를 관리하고, browser가 그릴 수 있는 canvas operation을 MQTT로 발행합니다. 이 서비스는 landmark나 카메라 이미지를 받지 않습니다.

## MQTT 통신

| 방향 | Topic | Payload |
| --- | --- | --- |
| Gesture → Canvas | `hgw/v1/gestures/{session_id}` | `GestureMessage` JSON |
| Canvas → 수신자 | `hgw/v1/canvas/{session_id}` | `CanvasMessage` JSON |

`MQTT_TOPIC_PREFIX`로 `hgw/v1`을 바꿀 수 있습니다. QoS는 0, retain은 false입니다. Canvas는 C가 보낸 `pointer`(normalized x/y)를 `CANVAS_WIDTH`×`CANVAS_HEIGHT` 픽셀 좌표로 변환합니다.

`DRAW_MOVE`와 `ERASE_MOVE`는 직전 점에 low-pass smoothing을 적용한 뒤 최대 8px 간격으로 중간 점을 보간합니다. Landmark 프레임이 드문 경우에도 선이 끊겨 보이지 않도록 하는 처리입니다. `CANVAS_SMOOTHING`(기본 `0.55`)과 `CANVAS_SEGMENT_PX`(기본 `8`)로 조절할 수 있습니다.

## 기능

- `DRAW_START`, `DRAW_MOVE`, `DRAW_END`
- `ERASE_START`, `ERASE_MOVE`, `ERASE_END`
- `UNDO`, `REDO`, `CLEAR`
- `ZOOM_START`, `ZOOM_UPDATE`, `ZOOM_END`
- 세션별 `sequence`, undo/redo history
- `GET /v1/sessions/{session_id}/export.png` PNG export

## 실행

Canvas는 MQTT broker가 준비된 상태에서 실행합니다. Broker와 같은 Docker network에 연결해야 합니다.

```bash
docker build -t hand-gesture-canvas ./services/canvas
docker run -d --name hgw-canvas --network hgw-net \
  -e MQTT_HOST=hgw-mqtt \
  -e MQTT_PORT=1883 \
  -e MQTT_TOPIC_PREFIX=hgw/v1 \
  -e CANVAS_WIDTH=1280 \
  -e CANVAS_HEIGHT=720 \
  hand-gesture-canvas
```

외부 포트는 열지 않습니다. `GET /healthz`와 HTTP apply/export endpoint는 Docker network 내부에서만 사용할 수 있습니다.

## 환경 변수

| 변수 | 기본값 | 설명 |
| --- | --- | --- |
| `MQTT_HOST` | `localhost` | MQTT broker host |
| `MQTT_PORT` | `11883` | MQTT broker port |
| `MQTT_TOPIC_PREFIX` | `hgw/v1` | MQTT topic prefix |
| `CANVAS_WIDTH` | `1280` | canvas 폭(px) |
| `CANVAS_HEIGHT` | `720` | canvas 높이(px) |
| `CANVAS_SMOOTHING` | `0.55` | 위치 완화 비율. 작을수록 더 부드러움 |
| `CANVAS_SEGMENT_PX` | `8` | 보간 점의 최대 간격(px) |
