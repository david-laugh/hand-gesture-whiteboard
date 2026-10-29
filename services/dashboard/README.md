# Dashboard — 파이프라인 실시간 지연 측정

gateway → vision → gesture → canvas 각 단계의 지연, FPS, 손실을 실시간으로 보여 주는 웹 대시보드입니다.
**MQTT를 구독만** 하고(발행하지 않음), vision의 `/metrics`를 읽기만 합니다. 다른 서비스는 수정하지 않습니다.

```text
휴대폰 ─uplink─► gateway ─gateway─► frames ─vision─► landmarks ─gesture─► gestures ─canvas─► canvas
                                      └──────────── dashboard가 hgw/v1/{frames,landmarks,gestures,canvas}/+ 구독 ─┘
```

## 실행

루트 compose(`hand-net` 네트워크와 MQTT 브로커 `hgw-mqtt`)가 떠 있는 상태에서, 저장소 루트에서 실행합니다.

```bash
docker compose -f services/dashboard/compose.yaml up -d --build
# → http://localhost:8020/
docker compose -f services/dashboard/compose.yaml down
```

- 루트 `compose.yaml`의 서비스 4개 구성을 바꾸지 않도록 **별도 compose 프로젝트**(`hgw-dashboard`)로 실행합니다. 이미 있는 `hand-net` 네트워크에 붙습니다.
- 대시보드도 Docker 안에서 실행하는 것을 권장합니다. gateway와 같은 시계를 쓰게 되어 `captured_at_ms`(gateway 시계 기준)와 비교할 때 오차가 생기지 않습니다.

| env | 기본값 | 설명 |
|---|---|---|
| `MQTT_HOST` / `MQTT_PORT` | `hgw-mqtt` / `1883` (compose), 코드 기본값 `localhost` / `11883` | 브로커 |
| `MQTT_TOPIC_PREFIX` | `hgw/v1` | 다른 서비스와 같아야 함 |
| `VISION_METRICS_URL` | `http://vision:8001/metrics` | vision 내부 단계 시간. 빈 값이면 읽지 않음 |
| `SETTLE_MS` | `2000` | 다음 단계 메시지를 기다리는 최대 시간. 지나면 "어디서 끊겼는지"로 분류 |
| `PUSH_INTERVAL_S` | `1` | 브라우저 갱신 주기 |
| `DASHBOARD_HOST_PORT` (compose) | `8020` | host 포트 (`127.0.0.1`에만 열림) |

## 측정 방식

모든 서비스가 `session_id`와 `frame_id`를 그대로 전달하므로, 네 토픽의 메시지를 이 두 값으로 묶습니다.
(`trace_id`는 중간에 바뀌므로 쓰지 않습니다.) 대시보드는 메시지를 **받은 시각을 하나의 시계로** 기록하고, 단계 사이의 차이를 계산합니다.

| 단계 | 계산 | 포함되는 것 |
|---|---|---|
| uplink (휴대폰 → gateway) | gateway `received_at_ms` − `captured_at_ms` | 휴대폰 인코딩 이후 네트워크/터널. 휴대폰 시계는 gateway ping으로 맞추므로 **ping 왕복의 절반 정도 오차** |
| gateway | frames 도착 − `received_at_ms` | gateway 대기열 + MQTT 발행 + 브로커 |
| vision | landmarks 도착 − frames 도착 | vision 대기 + 디코딩 + 추론 + 발행 + 브로커 |
| gesture | gestures 도착 − landmarks 도착 | gesture 처리 + 발행 + 브로커 |
| canvas | canvas 도착 − gestures 도착 | canvas 처리 + 발행 + 브로커 |
| 서버 구간 합계 | canvas 도착 − `received_at_ms` | gateway 수신 이후 전부 |
| 전체 | canvas 도착 − `captured_at_ms` | 촬영부터 canvas 결과까지 |

- **완료/손실 분류:** canvas 결과까지 오면 "완료"입니다. `SETTLE_MS` 안에 오지 않으면 처음 빠진 단계로 분류합니다: `vision 드롭`(latest-wins로 버려짐), `gesture 없음`, `canvas 없음`.
- **미매칭:** frames 없이 들어온 메시지(대시보드 시작 전 프레임, canvas 마우스 UI 입력 등)는 통계에서 제외하고 개수만 표시합니다.
- **Vision 내부 시간:** vision `/metrics`의 decode, inference, serialization, publish, queue_wait를 그대로 보여 줍니다(최근 1000프레임 기준).
- **통계:** 집계 구간(10/30/60/120초)의 p50, p95, 최대. 추이 그래프는 프레임 도착 초 단위 버킷입니다.

## 화면

- 상태: MQTT 연결, 현재 촬영 세션, 집계 대기 프레임 수, vision `/metrics` 수신 여부
- 요약 타일: 촬영 → canvas p50/p95, canvas 결과 FPS(카메라 입력 FPS 함께), 끝까지 처리된 비율
- **단계별 지연 구성:** 단계별 p50을 쌓은 막대. 각 조각에 마우스를 올리면 p50, p95, n 표시
- **촬영 → canvas 지연 추이:** 최근 120초 p50/p95 선 그래프. 마우스를 올리면 그 초의 단계별 p50 표시
- 단계별 상세 표, 토픽별 FPS·수신·미매칭, 손실률(✓ / ▲ / ✕ 아이콘과 글자로 표시)
- Vision 내부 처리 시간 표, 최근 프레임 20개(단계별 ms와 gesture)

밝은 모드와 어두운 모드를 지원합니다(OS 설정을 따름). 단계 색은 색각 이상 시뮬레이션 검증을 통과한 고정 순서입니다.

## 한계

- canvas 결과가 **브라우저 화면에 그려지는 시점**은 포함되지 않습니다. canvas 그림판이 렌더링 시각을 보고하지 않기 때문입니다.
- 각 단계 값에는 MQTT 브로커 경유 시간이 포함됩니다(같은 호스트에서 보통 1 ms 미만).
- QoS 0이라 부하가 크면 대시보드가 메시지를 놓칠 수 있습니다. 이 경우 손실로 집계될 수 있으니 토픽별 수신 수와 함께 보세요.
- 대시보드를 다시 시작하면 쌓인 기록은 초기화됩니다(메모리에만 보관, 최근 5분).

## API

| Method | Path | 설명 |
|---|---|---|
| GET | `/` | 대시보드 화면 |
| WS | `/ws/live` | 1초마다 snapshot 전송. `{"window_s": 10\|30\|60\|120}`을 보내면 집계 구간 변경 |
| GET | `/api/snapshot?window_s=30` | 같은 snapshot JSON (스크립트·기록용) |
| GET | `/healthz` | 상태 |

## 테스트

```bash
cd services/dashboard
python -m unittest discover -s tests -v     # fastapi, paho-mqtt 필요 (requirements.txt)
```
