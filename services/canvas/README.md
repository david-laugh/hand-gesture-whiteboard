# canvas (Container D)

> 상태: **Scaffold** — 애플리케이션 코드 없음. Container는 `sleep infinity`로 대기한다.

| 항목 | 값 |
|---|---|
| Port | `8003` (internal only) |
| 담당자 | _(TBD)_ |
| Dependencies | fastapi, uvicorn, pillow |

## 책임

- Gesture command 입력 → stroke state 관리
- Undo / Redo / Clear (history stack)
- Canvas operation 생성 (`stroke_*`, `erase_*`, `remove_stroke`, `restore_stroke`, `set_view`, `clear`)
- 최종 PNG export (Pillow로 stroke 재렌더링)
- `canvas_ms` 측정

## Input / Output

| | Schema |
|---|---|
| Input | `POST /v1/apply` — [`gesture.schema.json`](../../shared/contracts/gesture.schema.json) |
| Output | [`canvas.schema.json`](../../shared/contracts/canvas.schema.json) |
| Export | `GET /v1/sessions/{session_id}/export.png` → `image/png` |

## Rules

- **gesture command만 입력**받는다. landmark나 camera image를 받지 않는다.
- normalized pointer → canvas pixel 좌표 변환은 canvas가 담당한다 (`CANVAS_WIDTH`, `CANVAS_HEIGHT`, view transform).
- `sequence`는 session 내 단조 증가.

## 디렉터리

```
src/     # application code (예정)
tests/   # tests (예정)
```
