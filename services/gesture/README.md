# gesture (Container C)

> 상태: **Scaffold** — 애플리케이션 코드 없음. Container는 `sleep infinity`로 대기한다.

| 항목 | 값 |
|---|---|
| Port | `8002` (internal only) |
| 담당자 | _(TBD)_ |
| Dependencies | fastapi, uvicorn, numpy |

## 책임

- Landmark 입력 → **gesture state machine** → semantic command 출력
- Command: `IDLE`, `DRAW_START`, `DRAW_MOVE`, `DRAW_END`, `ERASE_START`, `ERASE_MOVE`, `ERASE_END`,
  `ZOOM_START`, `ZOOM_UPDATE`, `ZOOM_END`, `UNDO`, `REDO`, `CLEAR`
- Debounce / cooldown / pointer smoothing
- `gesture_ms` 측정

## Input / Output

| | Schema |
|---|---|
| Input | `POST /v1/classify` — [`landmark.schema.json`](../../shared/contracts/landmark.schema.json) |
| Output | [`gesture.schema.json`](../../shared/contracts/gesture.schema.json) |

## Rules

- **landmark만 입력**받는다. 이미지에 접근하지 않는다.
- session별 상태를 유지한다 (`session_id` key). `*_START` 후에는 반드시 `*_END`를 발행한다.
- frame_id gap(drop)에 견고해야 한다.

명세: [`docs/gesture-spec.md`](../../docs/gesture-spec.md)

## 디렉터리

```
src/     # application code (예정)
tests/   # tests (예정) — landmark 시퀀스 fixture 기반 state machine 테스트 권장
```
