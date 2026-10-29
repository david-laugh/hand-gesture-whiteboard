# gesture (Container C)

| 항목 | 값 |
|---|---|
| Port | `8002` (internal only, HTTP는 확인·테스트용) |
| 입력 | MQTT 구독 `{prefix}/landmarks/+` (WebSocket `ws://gesture:8002/ws/landmarks`는 기본 꺼짐) |
| 출력 | MQTT 발행 `{prefix}/gestures/{session_id}` — [`gesture.schema.json`](../../shared/contracts/gesture.schema.json) |
| QoS / retain | 0 / 없음 |
| Dependencies | fastapi, uvicorn, paho-mqtt |

## 책임

- Landmark 입력 → **gesture state machine** → semantic command 출력
- 현재 구현: 그리기 `DRAW_*`, 지우개 `ERASE_*`, 되돌리기 `UNDO`, 펜 색·굵기 변경 (아래 "제스처" 표)
- Debounce, pointer smoothing (One-Euro), `gesture_ms` 측정
- 메시지가 `SESSION_GAP_MS` 동안 끊기면 그리던 획에 `DRAW_END` 발행

## 입력 방식 (두 가지, 출력은 항상 MQTT)

| 방식 | 주소 | 켜고 끄기 | 비고 |
|---|---|---|---|
| MQTT | 구독 `{prefix}/landmarks/+` | `MQTT_INPUT_ENABLED` (기본 `true`) | **현재 vision의 발행 경로** |
| WebSocket | `ws://<host>:8002/ws/landmarks` | `WS_INPUT_ENABLED` (기본 `false`) | 예전 vision 경로. 필요할 때만 켬. C가 서버, vision이 client |

- 두 방식 모두 같은 어댑터와 처리 함수를 쓰고, 결과는 `{prefix}/gestures/{session_id}`로 MQTT 발행한다.
- WebSocket은 **단방향**: 텍스트 메시지 1개 = 프레임 1개, C는 응답을 보내지 않는다. 받은 순서대로 하나씩 처리한다.
- 잘못된 메시지는 에러 로그(`invalid_message`)만 남기고 건너뛴다. 연결은 끊지 않는다.
- WebSocket 연결이 끊기면 그 연결로 들어온 세션 중 그리는 중이던 세션에 `DRAW_END`를 발행한다.
  끊김 감시(`SESSION_GAP_MS`)와 겹쳐도 `DRAW_END`는 한 번만 나간다.
- `MQTT_ENABLED=false`면 MQTT 입력과 발행이 모두 꺼진다. WebSocket으로 받은 결과는 로그만 남는다.

## 입력 형식 (둘 다 받음)

| 구분 | 판별 | 비고 |
|---|---|---|
| 레포 형식 | `message_type` 필드 있음 | [`landmark.schema.json`](../../shared/contracts/landmark.schema.json). major 버전이 다르면 거부 |
| vision 형식 | `message_type` 없음, `type: "landmarks"` | [`services/vision/src/contract.py`](../vision/src/contract.py). `trace_id`가 없어서 `{session_id}-{frame_id}`로 만들고, `ts`(epoch ms)를 `timestamp_ms`로 씀 |

session_id에 `[A-Za-z0-9_-]` 이외의 문자가 있으면(vision 형식) `_`로 바꾸고 경고 로그를 남긴다.

## 제스처

| 손 모양 | 결과 | 비고 |
|---|---|---|
| 검지만 폄 | `DRAW_START` / `DRAW_MOVE` / `DRAW_END` | pointer = 검지 끝. START에 `params.color`, `params.brush_size`(px) |
| 집게 (엄지 끝 4와 검지 끝 8을 붙임) | `ERASE_START` / `ERASE_MOVE` / `ERASE_END` | pointer = 4와 8의 중간점. START에 `params.eraser_radius`(정규화). 다른 포즈보다 먼저 검사 |
| 주먹 0.7초 | `UNDO` (1번) | pointer `null`. 획이 끝난 시점부터 잼 |
| 브이 (검지 + 중지) 0.5초 | `IDLE` + `params.color` (1번) | 다음 펜 색. 세션별 순환, 이후 `DRAW_START`에 반영 |
| 손바닥 (네 손가락) 0.5초 | `IDLE` + `params.brush_size` 또는 `params.eraser_radius` (1번) | 마지막으로 쓴 도구의 굵기 순환 (펜 4/8/16px, 지우개 지름 20/30/50px) |

- **집게 판정**: dist(4,8) ÷ 손 크기. 진입 < `PINCH_ENTER_RATIO`(0.30), 지우는 중 유지 < `PINCH_EXIT_RATIO`(0.45).
  두 값을 달리해서(히스테리시스) 경계에서 시작/끝이 떨리지 않는다.
  주먹은 엄지가 접힌 검지 옆에 붙어 거리가 가까우므로, 검지가 `PINCH_INDEX_MIN_RATIO`(−0.15)보다 깊게 접혀 있으면 집게로 보지 않는다.
- **그리기와 지우기는 동시에 되지 않는다.** 도구를 바꾸면 이전 도구의 `*_END`가 먼저 나가고 새 도구의 `*_START`가 나간다.
- **일회성 명령** (주먹·브이·손바닥): 그리거나 지우는 중이 아닐 때만 나간다.
  - `COMMAND_HOLD_MS`(500) 동안 유지하면 1번 발행 → 같은 자세를 계속 유지하면 다시 나가지 않음 (자세를 바꿔야 재발행)
  - 명령 사이에는 `COMMAND_COOLDOWN_MS`(1000) 쿨다운
  - 주먹(UNDO)은 `UNDO_HOLD_MS`(700) 유지. 획이 끝난 시점부터 새로 잰다 → **그리다 주먹으로 잠깐 접으면 UNDO가 나가지 않고**, 집게·검지에서 주먹으로 바꿔 0.7초 이상 유지하면 UNDO
- **지우개 크기 변환**: canvas는 `eraser_radius × min(가로, 세로) × 2`를 지우개 지름(px)으로 쓴다 (1280×720 → 720).
  그래서 지름 30px → `eraser_radius = 30 / (2 × 720) ≈ 0.0208`. 캔버스 크기가 다르면 `CANVAS_MIN_SIDE_PX` 조정.
- 펜 색·굵기 설정은 세션에 남는다. 끊김 감시가 획을 닫아도 유지되고, `SESSION_TTL_S`(600초) 동안 메시지가 없으면 세션과 함께 사라진다.
- canvas 확인: `params.color`·`brush_size`는 `DRAW_START`, `eraser_radius`는 `ERASE_START`에서만 읽는다 (`services/canvas/src/engine.py`).

## 그리기 판별

- **손가락 펴짐**: (손목~손끝 거리 − 손목~둘째 마디 거리) ÷ 손 크기(손목~중지 시작 마디) > `FINGER_EXTENDED_RATIO`.
  y좌표를 비교하지 않으므로 손등 촬영, 손 크기 변화, 손 기울기에 영향이 적다.
- **그리기 자세**: 검지만 펴짐 (엄지는 무시). N프레임 연속이면 `DRAW_START`, 아닌 프레임이 M프레임 연속이면 `DRAW_END`.
  이탈을 기다리는 동안의 `DRAW_MOVE`와 `DRAW_END`의 pointer는 마지막 그리기 좌표로 고정 (획 끝 꼬리 방지).

### 손가락 번호 뒤집힘 보정 (`FLIP_FIX_ENABLED`)

손등을 찍으면 MediaPipe가 가끔 손가락 번호를 좌우로 뒤집어 붙인다 (검지 ↔ 새끼, 중지 ↔ 약지).
실제 녹화(rec01)에서 손이 잡힌 프레임의 약 30%가 이랬고, 검지를 편 자세가 "새끼만 폄"으로 판별되어 획이 끊겼다.

- handedness는 쓰지 않는다. 손목(0)·검지 시작 마디(5)·새끼 시작 마디(17)의 **회전 방향(외적 부호)**만 본다.
  이 부호는 화면 안에서 손이 돌아가도 그대로이고, 번호가 좌우로 뒤집힐 때만 바뀐다.
- 회전 방향이 `FLIP_NORMAL_SIGN`과 반대이고 **원래 번호로는 아무 포즈(집게·검지·브이·손바닥·주먹)도 안 나올 때만**,
  검지↔새끼·중지↔약지 번호를 바꿔 모든 포즈를 다시 판별한다. 원래 번호로 이미 포즈가 나오면 건드리지 않는다.
- 보정으로 나온 포즈의 pointer는 바뀐 번호 기준이다. 예: 그리기면 **원래 번호 20번 점(실제 검지 끝)**.
- 보정이 적용된 프레임은 로그에 `"flip_fixed": true`로 표시된다.
- 효과 (rec01): 짧은 선 구간의 그리기가 4프레임에서 37프레임으로 늘었다.

**한계**
- 실제로 **새끼만 편 자세**도 회전 방향이 반대로 판단되면 그리기로 인식될 수 있다.
- 회전 방향 판단은 손이 옆으로 서 있을 때 틀릴 수 있다 (rec01에서 MediaPipe handedness와 약 90% 일치).
  그래서 "원래 번호로 아무 포즈도 안 나올 때만" 보정한다.
- 촬영 환경이 바뀌면(왼손, 전면 카메라, 좌우 반전 등) 정상 회전 방향이 바뀔 수 있다 → `FLIP_NORMAL_SIGN` 확인.
- 실제로 약지+새끼만 편 자세도 브이로 인식될 수 있다 (새끼만 편 자세가 그리기로 인식되는 것과 같은 이유).

### 촬영 안내 (rec01 녹화 분석 결과)
- 원은 **화면 안쪽에서 작게** 그린다. 손목 쪽이 화면 가장자리를 넘으면 검지가 화면 안에 있어도 vision이 손을 놓친다
  (사라지기 직전 검지 끝 x ≈ 0.85).
- **밝은 곳**에서 찍는다. 빠른 지그재그는 흔들림 때문에 검출이 대부분 실패했다 (61프레임 중 14프레임만 검출).

## HTTP (확인·테스트용)

| Method & Path | 설명 |
|---|---|
| `GET /healthz` | `{"status":"ok","service":"gesture"}` |
| `POST /v1/classify` | 입력 메시지 → GestureMessage 응답 (MQTT로 발행하지 않음) |
| `POST /v1/sessions/{session_id}/reset` | 세션 상태 삭제, `204` |

## 환경변수

| 이름 | 기본값 | 설명 |
|---|---|---|
| `MQTT_ENABLED` | `true` | `false`면 MQTT 입력·발행 모두 끔 (HTTP·WS 입력만, 결과는 로그) |
| `MQTT_INPUT_ENABLED` | `true` | `{prefix}/landmarks/+` 구독 여부 |
| `WS_INPUT_ENABLED` | `false` | `/ws/landmarks` 엔드포인트 사용 여부 |
| `MQTT_HOST` | `hgw-mqtt` | 로컬 실행 시 `localhost` |
| `MQTT_PORT` | `1883` | 로컬 실행 시 `11883` |
| `MQTT_TOPIC_PREFIX` | `hgw/v1` | |
| `DRAW_ENTER_FRAMES` | `3` | 그리기 자세 N프레임 연속 → `DRAW_START` |
| `DRAW_EXIT_FRAMES` | `5` | 그리기 자세 아님 M프레임 연속 → `DRAW_END` (30fps에서 약 167ms) |
| `SESSION_GAP_MS` | `500` | 이 시간 동안 메시지가 없으면 세션 초기화 (그리는 중이면 `DRAW_END`) |
| `LAG_WARN_MS` | `200` | 처리 시각 − 입력 시각이 이보다 크면 경고 (세션당 1초에 1번) |
| `FINGER_EXTENDED_RATIO` | `0.15` | 손가락 "펴짐" 기준 |
| `FLIP_FIX_ENABLED` | `true` | 손가락 번호 뒤집힘 보정 (아래 참고) |
| `FLIP_NORMAL_SIGN` | `1` | 이 촬영 환경에서 정상인 회전 방향 (`1` 또는 `-1`, rec01 녹화 기준 `1`) |
| `PINCH_ENTER_RATIO` / `PINCH_EXIT_RATIO` | `0.30` / `0.45` | 집게 진입 / 유지 기준 (dist(4,8) ÷ 손 크기) |
| `PINCH_INDEX_MIN_RATIO` | `-0.15` | 검지가 이보다 깊게 접히면 집게 아님 (주먹 구분) |
| `COMMAND_HOLD_MS` / `COMMAND_COOLDOWN_MS` | `500` / `1000` | 브이·손바닥 유지 시간 / 명령 쿨다운 |
| `UNDO_HOLD_MS` | `700` | 주먹(UNDO) 유지 시간 (획이 끝난 시점부터) |
| `PEN_COLORS` | canvas 팔레트 8색 | 쉼표로 구분한 `#RRGGBB` 목록, 첫 번째가 기본색 |
| `BRUSH_SIZES_PX` / `BRUSH_DEFAULT_PX` | `4,8,16` / `8` | 펜 굵기 순환 |
| `ERASER_SIZES_PX` / `ERASER_DEFAULT_PX` | `20,30,50` / `30` | 지우개 지름 순환 (px) |
| `CANVAS_MIN_SIDE_PX` | `720` | 지우개 px → 정규화 반지름 변환용 canvas 짧은 변 |
| `SESSION_TTL_S` | `600` | 이만큼 조용한 세션은 메모리에서 삭제 |
| `ONE_EURO_MIN_CUTOFF` / `ONE_EURO_BETA` / `ONE_EURO_D_CUTOFF` | `1.0` / `10.0` / `1.0` | pointer 필터 |

## 로컬 실행

모든 명령은 `services/gesture` 폴더에서 실행한다.

```bash
python -m venv .venv            # Python 3.11~3.12 권장
.venv/Scripts/pip install -r requirements-dev.txt   # macOS/Linux: .venv/bin/pip
```

### 테스트

```bash
.venv/Scripts/python -m pytest -q
```

### 주의사항

- **로컬 실행 시 `MQTT_PORT=11883`을 꼭 지정한다.** 기본값은 1883이라, PC의 1883에 다른 mosquitto
  (예: Windows에 설치된 mosquitto 서비스)가 돌고 있으면 **오류 없이 엉뚱한 브로커에 붙는다.**
  그러면 C 로그에는 `mqtt_connected`가 찍히지만 메시지는 오가지 않는다.
- 끊김 감시(`SESSION_GAP_MS`)는 **서버 수신 시각** 기준이다. 휴대폰 시계(`ts`)가 서버와 몇 초 달라도 영향이 없다.
  `ts`는 프레임 간격 계산(필터, 첫 프레임 gap 판단)과 지연 경고(`LAG_WARN_MS`)에만 쓴다.
  시계 차이가 크면 지연 경고가 계속 나올 수 있다 (세션당 1초에 1번).

### B 없이 D(canvas) 개발하기

`send_sequence.py --mqtt`를 돌리면 C가 `hgw/v1/gestures/{session_id}`에 GestureMessage를 계속 발행한다.
D는 이 토픽을 구독하면 vision 없이 그리기 입력(DRAW_START → DRAW_MOVE… → DRAW_END)을 받을 수 있다.
반복이 필요하면 셸에서 루프로 돌린다.

```bash
while true; do .venv/Scripts/python dev/send_sequence.py dev-01 --mqtt; done
# PowerShell: while ($true) { .venv\Scripts\python dev\send_sequence.py dev-01 --mqtt }
```

### 브로커 + C + 가짜 B + 가짜 D

1. 로컬 MQTT 브로커 (gateway README와 같은 방식, 호스트 포트 11883)

   ```bash
   docker network create hgw-net   # 이미 있으면 생략
   docker run -d --name hgw-mqtt --network hgw-net -p 127.0.0.1:11883:1883 \
     eclipse-mosquitto:2 mosquitto -c /mosquitto-no-auth.conf
   ```

2. C 서버

   ```bash
   MQTT_HOST=localhost MQTT_PORT=11883 .venv/Scripts/python -m uvicorn src.main:app --port 8002
   # PowerShell: $env:MQTT_HOST="localhost"; $env:MQTT_PORT="11883"; .venv\Scripts\python -m uvicorn src.main:app --port 8002
   ```

3. 가짜 D: 발행된 gesture를 한 줄씩 출력

   ```bash
   .venv/Scripts/python dev/watch_gestures.py
   ```

4. 가짜 B: 손 없음 → 검지로 원 → 손가락 접기 → 손 없음 시퀀스 발행

   ```bash
   .venv/Scripts/python dev/send_sequence.py dev-01 --mqtt              # vision 형식 (기본)
   .venv/Scripts/python dev/send_sequence.py dev-01 --mqtt --format repo
   .venv/Scripts/python dev/send_sequence.py dev-01 --ws                # WebSocket (현재 vision과 같은 경로)
   .venv/Scripts/python dev/send_sequence.py dev-01 --http              # MQTT 없이 /v1/classify로
   ```

브로커 정리: `docker stop hgw-mqtt && docker rm hgw-mqtt`

## 디렉터리

```
src/
  main.py           FastAPI 앱, lifespan에서 MQTT 시작/종료
  mqtt_io.py        MQTT 구독·발행, 끊김 감시 스레드
  adapter.py        입력 형식(레포/vision) → 내부 모델
  processor.py      LandmarkMessage → GestureMessage (HTTP·MQTT 공용, lock)
  state_machine.py  IDLE ↔ DRAWING
  hand_pose.py      손가락 펴짐 판별
  one_euro.py       pointer 필터
dev/                send_sequence.py (가짜 B), watch_gestures.py (가짜 D)
dev/recordings/     로컬 녹화 원본·분석 결과 (git 제외, services/gesture/.gitignore)
tests/
tests/fixtures/rec01/  실제 손등 녹화에서 잘라낸 회귀 테스트 구간 (원 2바퀴, 짧은 선, 다시 넣기)
```

명세: [`docs/gesture-spec.md`](../../docs/gesture-spec.md)
