# gateway

브라우저 카메라 프레임을 받아 MQTT로 전송하는 Gateway입니다. 프레임을 처리하거나 다른 서비스에 요청하지 않습니다.

## 흐름

`Browser webcam → WebSocket /ws → MQTT {MQTT_TOPIC_PREFIX}/frames/{session_id}`

WebSocket과 MQTT의 payload 형식은 같습니다. `[uint32 BE metadata length][UTF-8 metadata JSON][JPEG bytes]`이며, 메타데이터는 `shared/contracts/frame.schema.json`을 따릅니다. JPEG는 JSON이나 로그에 넣지 않습니다.

각 WebSocket session은 대기 프레임을 하나만 유지합니다. 새 프레임이 도착하면 이전 대기 프레임을 버려 지연이 계속 쌓이지 않습니다.

## 실행

MQTT broker를 먼저 준비한 뒤 다음과 같이 실행합니다.

```bash
pip install -r requirements.txt
MQTT_HOST=localhost MQTT_PORT=1883 uvicorn src.main:app --host 0.0.0.0 --port 8000
```

`http://localhost:8000/`에서 카메라를 시작합니다. 카메라는 localhost 또는 HTTPS에서만 접근할 수 있습니다.

## 환경 변수

| 변수 | 기본값 | 설명 |
| --- | --- | --- |
| `MQTT_HOST` | `localhost` | MQTT broker 호스트 |
| `MQTT_PORT` | `11883` | MQTT broker 포트 |
| `MQTT_TOPIC_PREFIX` | `hgw/v1` | topic prefix |
| `GATEWAY_TIMEOUT` | `3` | Browser 전송 timeout(초) |
| `GATEWAY_MAX_FRAME_BYTES` | `2097152` | 허용할 최대 WebSocket 프레임 크기 |
| `GATEWAY_MAX_SESSIONS` | `16` | 동시 WebSocket session 상한 |
