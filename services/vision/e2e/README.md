# 전체 파이프라인 E2E 테스트 환경 (운영 경로)

실제 카메라 → **Gateway → Vision → Gesture → Canvas**를 모두 실제 코드로 MQTT로 연결해 띄워 두는 테스트 환경이다.
어느 서비스가 업데이트되든 같은 명령으로 다시 띄워 휴대폰으로 그려 보고, 실제 손동작 데이터를 녹화한다.

```text
휴대폰/노트북 카메라 ─► gateway /cam (localhost:8010)
   ─MQTT hgw/v1/frames/+─► vision ─MQTT hgw/v1/landmarks/+─► gesture ─MQTT hgw/v1/gestures/+─► canvas (localhost:8013)
                                              ├─► canvas (손 뼈대 + 촬영 세션 감지)
                                              └─► gesture-recorder ─► services/vision/recordings/*.jsonl
```

| 서비스 | 컨테이너 | 역할 |
|---|---|---|
| gateway | `hgw-gateway` | **팀원 코드 그대로** (`services/gateway` bind mount, Dockerfile CMD). host `127.0.0.1:8010` |
| vision | `hgw-vision` | 내 서비스. frames 구독 → 추론 → `hgw/v1/landmarks/{session_id}` 발행 |
| mqtt | `hgw-e2e-mqtt` (alias `hgw-mqtt`) | **테스트 전용** mosquitto. host `127.0.0.1:11883` |
| gesture-recorder | `hgw-e2e-gesture-recorder` | **테스트 전용**. C와 똑같이 `hgw/v1/landmarks/+`를 구독해 계약(topic↔session_id 포함)을 검증하고 session별로 녹화 |
| gesture | `hgw-gesture` | **팀원 코드 그대로** (Dockerfile CMD). `hgw/v1/landmarks/+` 구독 → `hgw/v1/gestures/{session_id}` 발행. WebSocket 입력은 끔 |
| canvas | `hgw-canvas` | **팀원 코드 그대로** (Dockerfile CMD). host `127.0.0.1:8013`. 그림판 `/`(최신 촬영 세션 자동 추적), 갤러리 `/gallery`. 그림은 `services/canvas/drawings/`에 자동 저장(git 제외), 파일 시각은 `TZ=Asia/Seoul` |

- 설정 파일: [`compose.e2e.yaml`](compose.e2e.yaml). 루트 `compose.yaml` **위에 덮어쓰는** override라서 팀원 파일은 수정하지 않는다.
- Host 8000은 다른 프로젝트가 쓰고 있어서 gateway를 **8010**에 연다. 변경: `E2E_GATEWAY_HOST_PORT=8020`.

## 1. 시작 / 상태 / 종료

저장소 루트에서 실행한다.

```bash
# 시작 (코드가 바뀌었으면 --build로 이미지 재빌드)
docker compose -f compose.yaml -f services/vision/e2e/compose.e2e.yaml up -d --build

# 상태
docker compose -f compose.yaml -f services/vision/e2e/compose.e2e.yaml ps
curl http://127.0.0.1:8010/healthz                       # gateway: mqtt_connected true
curl http://127.0.0.1:8013/healthz                       # canvas: mqtt_connected true, live_session (촬영 중인 세션)
docker compose exec vision python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8001/healthz').read().decode())"
#   → mqtt_connected true, landmarks_topic hgw/v1/landmarks/{session_id}

# 실시간 로그
docker logs -f hgw-e2e-gesture-recorder                  # 30프레임마다: 손 검출 비율, frame_id gap, fps, 녹화 파일
docker compose logs -f gesture                           # 프레임별 gesture 판정 (JSON 로그)
docker compose logs -f vision

# 종료 (테스트 전용 서비스까지 정리)
docker compose -f compose.yaml -f services/vision/e2e/compose.e2e.yaml down
```

> override 없이 `docker compose up -d`만 실행하면 원래 구성으로 돌아간다 (gateway·gesture·canvas는 placeholder, 브로커와 recorder 없음).
> 매번 `-f`를 쓰기 번거로우면 PowerShell `$env:COMPOSE_FILE="compose.yaml;services/vision/e2e/compose.e2e.yaml"`, bash `export COMPOSE_FILE=compose.yaml:services/vision/e2e/compose.e2e.yaml`.

## 2. 카메라 연결

**노트북 웹캠 (가장 간단):** 브라우저에서 `http://localhost:8010/cam` → **촬영 시작**. `localhost`는 secure context라서 HTTPS 없이도 카메라 권한을 받을 수 있다. 대시보드(`http://localhost:8010/`)에서 최신 프레임과 수신/발행 수를 볼 수 있다.

**휴대폰 (운영과 같은 후면 카메라):** 휴대폰에서 카메라를 쓰려면 HTTPS가 필요하다 (gateway README와 같은 방법).

```bash
cloudflared tunnel --url http://localhost:8010
```

출력된 `https://…trycloudflare.com` 주소를 휴대폰에서 열거나, 그 주소로 연 대시보드의 QR을 스캔한다. 터미널을 닫으면 터널도 닫힌다.
⚠️ 터널이 열려 있는 동안 이 주소는 **인터넷에 공개**된다. 테스트할 때만 열어 둔다.

**그림 확인 (노트북):** `http://localhost:8013/`을 열어 둔다. `session_id` 없이 열면 canvas가 **가장 최근 촬영 세션을 자동으로 따라간다** (촬영 시작마다 새 빈 그림판으로 바뀜). 손 뼈대도 같이 표시된다.
- 촬영을 멈추고 5초(`CANVAS_SESSION_IDLE_SEC`)가 지나면 그 세션 그림이 `services/canvas/drawings/`에 PNG로 저장되고 `http://localhost:8013/gallery`에 나온다.
- 특정 세션만 고정해서 보려면 `http://localhost:8013/?session_id=<id>` (recorder 로그에 세션마다 링크가 찍힌다).

카메라 없이 gesture → canvas만 확인하려면 녹화를 재생한다 (`services/vision`에서):

```bash
python -m tools.replay_landmarks recordings/<file>.jsonl --rebase-ts --session-id replay-test
```

## 3. 녹화 → Gesture 담당자에게 전달

Gateway의 **촬영 시작/중지 1회가 session 1개**이고, recorder는 session마다 파일을 하나씩 만든다.

1. 동작 하나당 한 번씩 촬영한다 (10~20초). 예: draw, erase, zoom, idle, no_hand, hand_enter_exit
   - 프로젝트 가정대로 **손등이 카메라를 향하게** 촬영
2. 녹화 목록과 품질 확인 (`services/vision`에서 실행):
   ```bash
   python -m tools.recordings            # msgs, 길이, fps, 손 검출 %, frame_id gap, 계약 오류, handedness
   python -m tools.recordings show <file>
   ```
3. 쓸 만한 녹화에 동작 이름을 붙여 샘플로 옮긴다 (계약 위반이 있으면 거부):
   ```bash
   python -m tools.recordings promote 20261029-153000_ab12cd34.jsonl draw
   ```
   → `samples/recorded/draw.jsonl`. `samples/recorded/README.md`에 동작, 카메라, 손등/손바닥 여부를 한 줄 적고 commit한다.

녹화 원본(`services/vision/recordings/`)은 git에 올리지 않는다. 각 줄은 **C가 실제로 받을 메시지 그대로**다. 드롭 때문에 생기는 frame_id gap, 실제 `ts` 간격, 960 해상도 기준 좌표가 모두 포함된다.

## 4. 업데이트 후 회귀 테스트

```bash
git pull                                                   # gateway/vision 최신 코드
docker compose -f compose.yaml -f services/vision/e2e/compose.e2e.yaml up -d --build
# 카메라 없이 전체 경로 확인 (gateway 역할로 MQTT 발행 → vision /metrics):
docker compose exec vision python -m tools.assets
docker compose exec vision python -m tools.bench mqtt --broker-host hgw-mqtt --broker-port 1883 --url http://127.0.0.1:8001
```

## 5. 실제 Gesture 서비스와 함께 쓰기

MQTT는 구독자가 여럿이어도 되므로 **설정 변경이 필요 없다.** Gesture(C)가 `hgw/v1/landmarks/+`를 구독하도록 구현되면,
recorder와 실제 gesture가 같은 메시지를 동시에 받는다. 녹화가 필요 없으면 `docker stop hgw-e2e-gesture-recorder`.

Gesture 담당자가 카메라 없이 개발할 때는 녹화 파일을 그대로 재생한다 (host 브로커 `localhost:11883`):

```bash
python -m tools.replay_landmarks samples/recorded/draw.jsonl --loop
```

자세한 연동 규격: [`../GESTURE_INTEGRATION.md`](../GESTURE_INTEGRATION.md)
