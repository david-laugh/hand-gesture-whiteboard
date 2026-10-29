# Gateway — 카메라 프레임 MQTT 송신기

Gateway는 브라우저의 카메라 JPEG 프레임을 받아 MQTT로 발행합니다. 프레임 추론, 손동작 판정, 캔버스 상태 관리는 하지 않으며, 이미지 데이터를 파일이나 로그에도 남기지 않습니다.

```text
Browser webcam ── WebSocket /ws ── Gateway ── MQTT ──► hgw/v1/frames/{session_id}
```

## Docker로 실행

Gateway와 MQTT broker가 통신할 Docker network를 만듭니다.

```bash
docker network create hgw-net
docker run -d --name hgw-mqtt --network hgw-net -p 127.0.0.1:11883:1883 eclipse-mosquitto:2 mosquitto -c /mosquitto-no-auth.conf
docker build -t hand-gesture-gateway ./services/gateway
```

Linux/macOS에서는 다음 명령으로 gateway를 실행합니다.

```bash
docker run --rm --name hgw-gateway --network hgw-net -p 8000:8000 \
  -e MQTT_HOST=hgw-mqtt \
  -e MQTT_PORT=1883 \
  -e MQTT_TOPIC_PREFIX=hgw/v1 \
  -e SCHEMA_VERSION=1.0.0 \
  hand-gesture-gateway
```

로컬 서버를 외부 휴대폰에서 열려면 별도의 HTTPS 주소가 필요합니다. Gateway를 실행한 상태에서 반드시 다음 명령으로 Cloudflare Tunnel을 시작합니다.

```bash
cloudflared tunnel --url http://localhost:8000
```

명령이 출력한 `https://…trycloudflare.com` 주소를 휴대폰 브라우저에서 엽니다. 대시보드의 QR 코드는 이 주소의 `/cam`을 가리키므로 휴대폰으로 바로 스캔할 수 있습니다. 페이지는 HTTPS일 때 자동으로 `wss://` WebSocket을 사용하므로 카메라 권한을 요청할 수 있습니다. Tunnel을 실행 중인 터미널을 종료하면 외부 주소도 닫힙니다. MQTT broker만 중지하고 싶다면 `docker stop hgw-mqtt`를 실행합니다.

로컬 Python 실행도 가능합니다.

```bash
cd services/gateway
pip install -r requirements.txt
MQTT_HOST=localhost MQTT_PORT=11883 uvicorn src.main:app --host 0.0.0.0 --port 8000
```

## 통신 규격

### Browser → Gateway

브라우저는 `ws://<host>:8000/ws/stream`에 연결합니다. `/ws`도 기존 호환 경로로 유지합니다. 연결이 성공하면 gateway는 다음 hello 메시지를 보냅니다.

```json
{"message_type":"hello","schema_version":"1.0.0","session_id":"<gateway-issued-id>","transport":"mqtt"}
```

카메라 페이지는 기존 C1 카메라 규격을 유지합니다. WebSocket binary 메시지는 다음 순서이며 정수는 big-endian입니다.

```text
[ uint64: captured_at_ms ][ JPEG bytes ]
```

`captured_at_ms`는 브라우저 캡처 시각의 Unix epoch 밀리초입니다. 페이지는 5초마다 ping/pong을 보내 gateway 시계와의 오차를 보정합니다. JPEG만 보내도 되며, 이때 gateway는 수신 시각을 `captured_at_ms`로 사용합니다.

이전 browser client와의 호환을 위해 아래 metadata packet도 계속 받을 수 있습니다.

```text
[ uint32: metadata JSON byte length ][ UTF-8 metadata JSON ][ JPEG bytes ]
```

이 형식에서 브라우저가 제공할 수 있는 metadata 필드는 아래뿐입니다. 다른 필드는 gateway가 거부합니다.

| 필드 | 형식 | 의미 |
| --- | --- | --- |
| `frame_id` | integer ≥ 0 | WebSocket session 안에서 증가하는 번호 |
| `captured_at_ms` | integer | 브라우저가 프레임을 캡처한 Unix epoch 밀리초 |
| `width`, `height` | integer > 0 | JPEG 원본 크기 |
| `jpeg_quality` | integer 0–100, 선택 | 브라우저 JPEG 인코더 품질 |
| `mirrored` | boolean, 선택 | 인코딩 전에 좌우 반전했는지 여부 |

Gateway는 JPEG 시작 마커와 SOF 헤더의 실제 폭·높이를 확인합니다. timestamp+JPEG 형식의 `frame_id`는 gateway가 세션별로 순차 발급합니다. 프레임 크기는 기본 2 MiB 이하이며, metadata packet의 `frame_id`가 이전 값보다 크지 않거나 metadata와 JPEG 크기가 다르면 error JSON을 보냅니다.

### Gateway → MQTT

Gateway는 매 프레임마다 `MQTT_TOPIC_PREFIX/frames/{session_id}`로 QoS 0, retain false로 발행합니다. 기본 topic은 `hgw/v1/frames/{session_id}`입니다.

MQTT payload는 Browser WebSocket과 달리 metadata를 포함하는 binary packet입니다. JPEG는 base64 JSON에 넣지 않으므로 크기가 불필요하게 증가하지 않습니다. Gateway가 채우는 최종 metadata는 `shared/contracts/frame.schema.json`을 따릅니다.

| Gateway가 추가하는 필드 | 의미 |
| --- | --- |
| `schema_version` | contract 버전. major 버전이 다르면 거부 |
| `message_type` | 항상 `frame` |
| `trace_id` | 프레임마다 새로 발급하는 UUID hex 문자열 |
| `session_id` | gateway가 발급한 WebSocket session ID |
| `timestamp_ms` | FrameMessage 생성 시각 |
| `received_at_ms` | gateway가 WebSocket에서 수신한 시각 |
| `content_type` | 항상 `image/jpeg` |
| `byte_length` | JPEG byte 수 |

수신 서비스는 topic을 구독한 뒤 처음 4 byte로 metadata 길이를 읽고, 그 길이만큼 UTF-8 JSON을 파싱한 후 나머지를 JPEG binary로 처리하면 됩니다. `trace_id`, `session_id`, `frame_id`는 이후 처리 단계에서도 그대로 유지해야 합니다.

### Gateway → Browser 오류

잘못된 입력은 `message_type: "error"` JSON으로 알립니다. `INVALID_FRAME`은 packet·JPEG·frame ID 검증 실패이며, `PUBLISH_ERROR`는 broker 미연결 또는 publish 실패를 뜻합니다. Gateway는 MQTT 소비자의 응답이나 ack를 기다리지 않는 단방향 송신기입니다.

## 흐름 제어와 상태

각 WebSocket session은 처리 대기열에 최신 프레임 1개만 보관합니다. 새 프레임이 들어왔을 때 이전 프레임이 아직 MQTT에 발행되지 않았으면 이전 프레임을 버리고 새 프레임으로 교체합니다. 이 latest-wins 방식으로 카메라 FPS가 broker 전송보다 빨라도 오래된 영상 때문에 지연이 누적되지 않습니다.

`GET /`는 MQTT 연결과 프레임 수, 메모리에 보관한 최신 수신 JPEG 1장을 보여 주는 대시보드이며, **카메라 열기**로 `/cam`에 이동합니다. 최신 프레임은 파일로 저장하지 않고 `/ws/status`로 대시보드에만 전달합니다. `/cam`은 실제 카메라 전송 화면입니다. `GET /healthz`는 MQTT 연결 상태를 포함한 health 응답을, `GET /api/metrics`는 수신·발행·drop·오류 수와 FPS를 반환합니다.

## 환경 변수

| 변수 | 기본값 | 설명 |
| --- | --- | --- |
| `MQTT_HOST` | `localhost` | MQTT broker 호스트. Docker network 안에서는 `hgw-mqtt` |
| `MQTT_PORT` | `11883` | MQTT broker 포트. Docker network 안에서는 `1883` |
| `MQTT_TOPIC_PREFIX` | `hgw/v1` | 발행 topic prefix. `+`, `#`, NUL 문자는 허용하지 않음 |
| `SCHEMA_VERSION` | `1.0.0` | frame contract 버전 |
| `GATEWAY_PUBLIC_URL` | 요청 URL | QR 코드가 가리킬 공개 주소. Tunnel 주소를 고정해 쓸 때 지정 |
| `GATEWAY_TIMEOUT` | `3` | Browser로 JSON을 보낼 때의 timeout(초) |
| `GATEWAY_MAX_FRAME_BYTES` | `2097152` | 허용할 최대 WebSocket binary 메시지 크기 |
| `GATEWAY_MAX_SESSIONS` | `16` | 동시 WebSocket session 상한 |

## 검증

```bash
PYTHONPATH=services/gateway python -m unittest discover -s services/gateway/tests -v
```
