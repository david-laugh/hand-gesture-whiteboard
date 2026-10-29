# vision (Container B)

카메라 프레임(JPEG)에서 MediaPipe로 손 랜드마크 21개를 추출해, 합의된 JSON으로 **Gesture(C)에 WebSocket으로 전송**한다.
Gesture 판정(Draw/Eraser/Zoom/Undo …)은 하지 않는다. `gesture`는 항상 `null`.

| 항목 | 값 |
|---|---|
| Port | `8001` (internal only, host 미노출) |
| 담당자 | _(TBD)_ |
| MediaPipe API | **Tasks `HandLandmarker`** (`mediapipe.tasks.python.vision`), **VIDEO** running mode |
| mediapipe | 1.2.0 (Docker: Python 3.11.18) |
| Model | 공식 `hand_landmarker.task` float16 **v1**, sha256 `fbc2a30080c3c557093b5ddfc334698132eb341044ccee322ccf8bcf3607cde1` |
| 출력 | `ws://gesture:8002/ws/landmarks` (C = server, B = client) |

## 1. API 선택 근거

| 확인 항목 | 결과 (2026-10-29 실제 환경) |
|---|---|
| `mp.solutions.hands.Hands` | **사용 불가** — mediapipe 1.2.0에 `mp.solutions` 자체가 없음 (`AttributeError`). 호스트(Py 3.12)와 컨테이너(Py 3.11) 모두 동일 |
| `mediapipe.tasks.python.vision.HandLandmarker` | 사용 가능 (`IMAGE` / `VIDEO` / `LIVE_STREAM`) |

**Running mode = VIDEO**를 선택했다.

- VIDEO는 동기 호출: `detect_for_video(image, ts)`가 **그 프레임의 결과**를 반환하므로 `frame_id` ↔ 결과 대응이 정확하다.
- LIVE_STREAM은 MediaPipe 내부에서 프레임을 조용히 버리고 callback으로 결과를 준다. 드롭을 우리가 셀 수 없고 대응 관리가 복잡하다.
- 대신 프레임 드롭은 Vision이 직접 한다 (§4 latest-wins). 모든 드롭이 `dropped_frame_count`로 집계된다.
- VIDEO mode는 프레임 간 tracking 상태를 가지므로 **session별 landmarker 인스턴스**를 두고(LRU 최대 `MAX_SESSIONS`), MediaPipe용 timestamp는 session 안에서 강제로 단조 증가시킨다. 출력 `ts`는 원본 값을 그대로 쓴다.

## 2. MediaPipe 설정

| 설정 | 값 | env |
|---|---|---|
| 최대 손 개수 | 1 | `MAX_NUM_HANDS` |
| Detection confidence | 0.5 | `MIN_HAND_DETECTION_CONFIDENCE` |
| Presence confidence | 0.5 | `MIN_HAND_PRESENCE_CONFIDENCE` |
| Tracking confidence | 0.5 | `MIN_TRACKING_CONFIDENCE` |
| 좌우 반전 | 적용 안 함 | – |
| Handedness 라벨 반전 | false | `SWAP_HANDEDNESS` |
| Delegate | CPU (TFLite XNNPACK) | – |

### Handedness 해석 주의

- MediaPipe 문서는 handedness가 **좌우 반전된(selfie) 입력을 가정**한다고 설명한다. 우리는 반전하지 않는다.
- 공식 테스트 이미지(반전 없음, 손바닥이 보임)에서는 피사체의 **실제 왼손이 `Left`(0.95)** 로 나왔다.
- 프로젝트는 **손등을 촬영**하는 환경을 가정한다. 손등과 손바닥은 2D상 거울상이라 라벨이 뒤집힐 수 있다. **이 조건은 아직 실제 카메라로 검증하지 않았다.**
- 검증 방법: `python -m tools.visual_check --source 0` 실행 → **오른손 손등**을 카메라에 보여 화면 라벨 확인 → `Left`로 나오면 `.env`에 `VISION_SWAP_HANDEDNESS=true`.
- `score`는 MediaPipe **handedness 분류 점수**다. 손 검출 confidence가 아니다 (Tasks API는 손별 검출 점수를 제공하지 않는다).

## 3. 출력: Vision → Gesture (합의된 계약)

```json
{
  "type": "landmarks",
  "session_id": "session-001",
  "frame_id": 1,
  "ts": 1793235000123,
  "hands": [
    { "handedness": "Right", "score": 0.95, "landmarks": [[0.5, 0.5, 0.0], "... 21개"] }
  ],
  "gesture": null
}
```

- WebSocket **text** message 1개 = 프레임 1개. C가 server(`ws://gesture:8002/ws/landmarks`), B가 client.
- `session_id`, `frame_id`, `ts`: 입력 프레임 값 그대로.
- `hands`: 최대 1개. 손이 없으면 `[]` (**실제로 추론한 프레임만** 보냄).
- `landmarks`: 정확히 21개 `[x, y, z]`, MediaPipe 순서 (0 WRIST, 4 THUMB_TIP, 8 INDEX_TIP, 12 MIDDLE_TIP, 16 RING_TIP, 20 PINKY_TIP).
- `x, y`: 이미지 기준 정규화 좌표. **clamp하지 않는다.** 손이 화면 밖에 걸치면 0 미만이나 1 초과가 나올 수 있다.
- `z`: MediaPipe 상대 깊이(손목 기준, 작을수록 카메라에 가까움). 정규화하지 않고 그대로 보낸다.
- 드롭된 프레임, 디코딩 실패 프레임은 **보내지 않는다.** 따라서 C는 `frame_id`가 건너뛸 수 있다고 가정해야 한다.
- 검증 함수: `src/contract.py: validate_landmarks_message()`. 샘플: [`samples/`](samples/).

### 연결 안정성

- 연결 실패나 끊김을 감지하면 `WS_RECONNECT_INTERVAL_S`(기본 2초) 간격으로 계속 재연결한다. 같은 오류 로그는 30초마다 한 번만 WARNING으로 남긴다.
- 연결되어 있지 않을 때 나온 결과는 **버퍼링하지 않고** `ws_unsent_count`로 집계한다. 실시간 데이터라 오래된 결과는 의미가 없기 때문이다. 이렇게 해서 "C 연결 실패"와 "손 미검출"을 구분한다.
- 내부 큐(기본 64)가 가득 차면 가장 오래된 메시지를 버린다.
- ping은 10초 간격으로 보낸다. 서비스 종료 시에는 close handshake를 수행한다.

## 4. 입력: Gateway → Vision

Gateway에 아직 입력 구현이 없어서 Vision이 **독립적인 입력 인터페이스**를 제공한다. 이미지는 **raw JPEG binary**로 받는다. base64는 쓰지 않는다.

메타데이터: `session_id`(string), `frame_id`(int ≥ 0), `ts`(int, epoch ms), `encoding`(`"jpeg"`), `width`, `height`(선택. 실제 크기는 디코딩 결과를 따름)

| 방식 | Endpoint | 형식 | 결과 |
|---|---|---|---|
| **스트리밍 (권장)** | `WS /ws/frames` | binary message: `[uint32 BE meta_len][meta JSON utf-8][JPEG bytes]` | C로 WS 전송. 성공 시 응답 없음, 오류 시 `{"type":"error",...}` |
| 스트리밍 | `POST /v1/frames` | body = JPEG, `Content-Type: image/jpeg`, headers `X-Session-Id`, `X-Frame-Id`, `X-Ts`, [`X-Width`, `X-Height`] | `202 {"accepted", "frame_id", "replaced_frame_id"}`, 결과는 C로 WS 전송 |
| 동기 (테스트/디버그) | `POST /v1/detect` | `/v1/frames`와 동일 | `200` + 계약 JSON 본문 (C로 보내지 않음). 단계별 시간은 `X-Decode-Ms`, `X-Inference-Ms`, `X-Serialization-Ms` 헤더로 반환 |

오류 응답: `400`(메타 누락·형식 오류, JPEG가 아님), `415`(Content-Type이 `image/jpeg`가 아님).

Gateway 예시 (Python, httpx):

```python
await client.post("http://vision:8001/v1/frames", content=jpeg_bytes, headers={
    "Content-Type": "image/jpeg", "X-Session-Id": sid, "X-Frame-Id": str(fid), "X-Ts": str(ts)})
```

**Latest-wins:** session마다 대기 슬롯이 1개다. 처리 중에 새 프레임이 오면 대기 중이던 프레임을 교체하고 `dropped_frame_count`를 올린다. 이렇게 해서 처리가 밀려도 지연이 누적되지 않는다.

기타: `GET /healthz`, `GET /metrics`, `GET /metrics/frames?limit=N`(frame_id별 기록), `POST /metrics/reset`, `DELETE /v1/sessions/{id}`(tracking 상태 초기화).

## 5. 성능 측정

`GET /metrics` 항목:

| 항목 | 의미 |
|---|---|
| `input_frame_count` | 받은 프레임 수 (유효한 것) |
| `processed_frame_count` | 실제로 추론까지 끝난 프레임 수 |
| `dropped_frame_count` | latest-wins로 교체되어 추론하지 않은 프레임 수 |
| `detected_frame_count` / `no_hand_frame_count` | 추론 결과 손 있음 / 없음 |
| `invalid_frame_count` / `failed_frame_count` | 입력 오류 / 추론 예외 |
| `ws_sent_count` / `ws_unsent_count` / `ws_send_error_count` | C로 전송 성공 / 미연결·큐 초과로 미전송 / 전송 중 끊김 |
| `input_fps` / `processing_fps` | 최근 5초 동안의 입력 FPS / 추론 완료 FPS (서로 다른 값) |
| `timings_ms.decode_ms` | JPEG 디코딩 + BGR→RGB |
| `timings_ms.inference_ms` | `detect_for_video()` 호출 시간만 |
| `timings_ms.serialization_ms` | 계약 dict 생성 + `json.dumps` |
| `timings_ms.websocket_send_ms` | `await ws.send()` 시간 (송신 버퍼 기록까지, 수신 확인은 포함하지 않음) |
| `timings_ms.queue_wait_ms` / `processing_ms` | 수신부터 처리 시작까지 / decode + inference + serialize |

각 timing은 최근 1000개 기준 `avg`, `p50`, `p95`, `max`를 제공한다. `/metrics/frames`로 frame_id별 단계 시간과 처리 상태(`processed`, `dropped`, `invalid`)를 확인할 수 있다.

벤치마크:

```bash
# 호스트
python -m tools.bench offline --frames 300          # 처리 용량 (네트워크 없음)
python -m tools.bench live --fps 30 --seconds 10    # latest-wins + mock C로 WS 전송
# 컨테이너 안 (실행 중인 서비스 대상)
docker compose exec vision python -m tools.assets   # 테스트 이미지 다운로드 (bind mount)
docker compose exec vision python -m tools.bench http --url http://127.0.0.1:8001 --fps 30 --seconds 10
```

CPU와 Memory는 벤치마크를 돌리는 동안 따로 측정한다.

```bash
docker stats hgw-vision                                     # 실시간
docker stats --no-stream --format "{{.Name}} {{.CPUPerc}} {{.MemUsage}}" hgw-vision
```

## 6. 로컬 시각화 테스트 (호스트 전용)

Docker에서는 웹캠에 접근하기 어렵다. 그래서 호스트 Python으로 실행한다. 영상에 skeleton을 합성하는 것은 **검증용일 뿐이고**, 실제 서비스는 좌표만 보낸다. skeleton은 Gateway/Frontend가 그린다.

```bash
cd services/vision
python -m venv .venv && .venv/Scripts/activate        # macOS/Linux: source .venv/bin/activate
pip install -r requirements-dev.txt
python -m tools.assets                                 # 공식 모델 + 테스트 이미지 (sha256 검증, 실패 시 예외)
python -m tools.visual_check --source 0               # 웹캠: 21점 + skeleton + handedness + FPS, q로 종료
python -m tools.visual_check --source 0 --dump my_hand.jsonl                              # 실제 손 데이터 녹화
python -m tools.visual_check --source 0 --gesture-url ws://localhost:8002/ws/landmarks    # C로 실시간 전송
```

`cv2.imshow`를 쓰려면 GUI가 포함된 OpenCV 빌드가 필요하다. mediapipe가 `opencv-contrib-python`을 함께 설치하고, Windows 호스트 venv의 cv2 빌드 정보에 `GUI: WIN32UI`가 포함된 것까지는 확인했다. 실제 창 표시는 아직 확인하지 않았다. 창이 뜨지 않으면 `pip install --force-reinstall opencv-contrib-python`.

## 7. 테스트

```bash
cd services/vision
pip install -r requirements-dev.txt && python -m tools.assets
VISION_REQUIRE_MODEL=1 python -m pytest -q     # 모델/이미지가 없으면 skip 대신 실패
python -m tests.mock_gesture_server --port 8002   # 수동 테스트용 mock C (계약 검증 + 출력)
```

| 파일 | 내용 | 실제 추론 여부 |
|---|---|---|
| `tests/test_contract.py` | 계약 빌더와 검증기, 입력 파싱 | ❌ 합성 값 (단위 테스트) |
| `tests/test_inference.py` | 손 검출, 21개, x·y 범위, `hands: []`, id 보존, JSON, VIDEO 연속 프레임, 모델 로딩 실패 | ✅ 실제 모델 |
| `tests/test_websocket.py` | mock C 전송, 연결 실패 → 재연결, 끊김 감지 | ❌ 합성 메시지 |
| `tests/test_service.py` | FastAPI 전체 경로 + mock C, 연속 입력 FPS와 드롭 집계 | ✅ 실제 모델 |

`tests/mock_gesture_server.py`는 테스트 전용이다. 서비스 코드(`src/`)는 이 파일을 import하지 않고, Docker image에서도 제외한다(`.dockerignore`).

## 8. Docker

```bash
docker compose build vision && docker compose up -d vision
docker compose ps vision            # (healthy) 확인
docker compose logs -f vision
```

- 모델은 **빌드 시** 공식 URL(버전 고정 v1)에서 `/opt/models`로 내려받고 checksum을 검증한다. 다운로드나 checksum이 실패하면 빌드가 실패한다. `/app` bind mount에 가려지지 않는 위치다.
- 모델 로딩에 실패하면 `ModelLoadError`로 startup이 실패하고 uvicorn이 종료된다. 이때 healthcheck가 실패하고 `restart: unless-stopped` 정책으로 재시작이 반복되므로 문제가 드러난다.
- mediapipe 1.2.0 Tasks 런타임은 CPU 추론에서도 `libEGL.so.1`을 요구한다. 그래서 `libegl1`, `libgles2`를 설치한다(실제로 확인한 오류).
- 코드 수정은 bind mount로 반영되지만 `--reload`가 없으므로 `docker compose restart vision`이 필요하다.

| env | 기본값 |
|---|---|
| `GESTURE_WS_URL` | `ws://gesture:8002/ws/landmarks` |
| `PUBLISH_ENABLED` | `true` |
| `WS_RECONNECT_INTERVAL_S` / `WS_OPEN_TIMEOUT_S` / `WS_QUEUE_SIZE` | `2` / `3` / `64` |
| `MAX_SESSIONS` | `4` |
| `MODEL_PATH` | `/opt/models/hand_landmarker.task` |

## 9. 디렉터리

```
src/        config, contract(출력 계약), frame_input(입력 파싱), detector(MediaPipe),
            pipeline(latest-wins), publisher(WS client), metrics, main(FastAPI)
tests/      pytest + mock_gesture_server (테스트 전용)
tools/      assets(공식 모델/이미지 다운로드), visual_check, generate_samples, bench, frames
samples/    실제 추론 결과 JSON (출처: samples/README.md)
models/     로컬 개발용 모델 (git 제외)
```
