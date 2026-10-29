# vision (Container B)

> 상태: **Scaffold** — 애플리케이션 코드 없음. Container는 `sleep infinity`로 대기한다.

| 항목 | 값 |
|---|---|
| Port | `8001` (internal only) |
| 담당자 | _(TBD)_ |
| Dependencies | fastapi, uvicorn, mediapipe, numpy, opencv-python-headless |

## 책임

- MediaPipe **Hand Landmarker** 실행
- 입력: JPEG frame (+ FrameMessage meta)
- 출력: hand당 21개 normalized `x`/`y`/`z` landmark, `handedness`, `confidence`
- `vision_ms` (decode + inference + serialization) 측정

## Input / Output

| | Schema |
|---|---|
| Input | `POST /v1/detect` — multipart: `meta` = [`frame.schema.json`](../../shared/contracts/frame.schema.json), `image` = image/jpeg |
| Output | [`landmark.schema.json`](../../shared/contracts/landmark.schema.json) |

## Rules

- **landmark data만 반환**한다. 이미지(원본/annotated)를 응답에 포함하지 않는다.
- 이미지를 디스크에 저장하지 않는다.
- `trace_id` / `session_id` / `frame_id`를 그대로 응답에 복사한다.
- Stateless: session 상태를 갖지 않는다 (모델 인스턴스만 유지).

## Model

`models/` 디렉터리에 MediaPipe 모델 파일을 둔다 (git ignore 대상).

```bash
curl -L -o services/vision/models/hand_landmarker.task \
  https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/latest/hand_landmarker.task
```

Container 내부 경로: `/app/models/hand_landmarker.task` (`MODEL_PATH` 환경변수)

## 디렉터리

```
models/  # hand_landmarker.task (download, not committed)
src/     # application code (예정)
tests/   # tests (예정)
```
