# Benchmark Plan

> 상태: **Scaffold / Plan**. 측정 도구는 구현 단계에서 각 서비스에 추가한다.
> 외부 모니터링 스택(Prometheus/Grafana 등)은 사용하지 않는다. 측정값은 **message field + 구조화 로그(JSON lines)** 로 수집한다.

## 1. 지표 정의

| 지표 | 단위 | 측정 위치 | 정의 |
|---|---|---|---|
| `gateway_ms` | ms | gateway | gateway 내부 처리 시간 = `server_total_ms − (vision_rtt_ms + gesture_rtt_ms + canvas_rtt_ms)` (WS 수신 파싱, orchestration, 직렬화) |
| `vision_ms` | ms | vision | JPEG decode + MediaPipe inference + 응답 직렬화. `LandmarkMessage.vision_ms` |
| `gesture_ms` | ms | gesture | state machine 처리 시간. `GestureMessage.gesture_ms` |
| `canvas_ms` | ms | canvas | operation 생성 + 상태 갱신. `CanvasMessage.canvas_ms` |
| `e2e_ms` | ms | browser | `rendered_at_ms − captured_at_ms` (둘 다 **browser clock** → clock skew 없음) |
| `input_fps` | frame/s | browser, gateway | Browser가 전송한 / gateway가 수신한 frame 수 per second |
| `vision_fps` | frame/s | gateway | vision 처리를 완료한 frame 수 per second |
| `drop_rate` | % | gateway | `dropped_frames / received_frames` (latest-wins 교체 + 오류 drop 포함) |
| CPU usage | % | docker | container별 CPU 사용률 (`docker stats`) |
| Memory usage | MiB | docker | container별 RSS (`docker stats`) |
| payload size | bytes | 각 구간 | frame JPEG(`byte_length`), LandmarkMessage, GestureMessage, CanvasMessage, gateway→browser result JSON 크기 |

보조 지표:

- `*_rtt_ms`: gateway가 측정한 내부 HTTP 왕복 시간. `rtt − service_ms` = 네트워크 + 직렬화 overhead.
- `network_overhead_ms` = `e2e_ms − server_total_ms` (browser ↔ gateway 왕복 + browser 렌더링).

## 2. Trace 기반 수집

- 모든 지표는 `trace_id` / `session_id` / `frame_id`를 key로 기록한다.
- 각 서비스는 요청마다 JSON log 한 줄을 stdout으로 출력한다:

  ```json
  {"ts":1793260800141,"service":"gesture","trace_id":"7f3c...","session_id":"sess-01","frame_id":1042,"gesture_ms":0.6,"req_bytes":2210,"resp_bytes":310}
  ```
- 분석 시 `docker compose logs --no-color > run.log` 후 `trace_id`로 join 하여 구간별 latency breakdown을 만든다.
- Browser는 frame별 `e2e_ms`를 수집해 benchmark 종료 시 JSON으로 다운로드한다 (구현 예정).

## 3. 시나리오

| ID | 시나리오 | 설정 | 목적 |
|---|---|---|---|
| B1 | Idle (손 없음) | 640×480, JPEG q=70, 30 fps | baseline / vision 최소 비용 |
| B2 | 한 손 Draw 연속 | 640×480, q=70, 30 fps, 60 s | 주 사용 시나리오 |
| B3 | 해상도 비교 | 320×240 / 640×480 / 1280×720 | 해상도 vs vision_ms vs 정확도 |
| B4 | JPEG 품질 비교 | q = 50 / 70 / 90 | payload size vs latency |
| B5 | 입력 fps 비교 | 15 / 30 / 60 fps | drop_rate, backpressure 동작 확인 |
| B6 | 양손 | 2 hands | vision_ms 증가량 |

각 시나리오는 warm-up 10 s 후 60 s 측정, 3회 반복.

## 4. 목표 (초안)

| 지표 | 목표 | 비고 |
|---|---|---|
| `e2e_ms` | p50 ≤ 100 ms, p95 ≤ 150 ms | localhost, 640×480 |
| `vision_ms` | p50 ≤ 30 ms | CPU only |
| `gesture_ms` | p95 ≤ 2 ms | |
| `canvas_ms` | p95 ≤ 5 ms | |
| `gateway_ms` | p95 ≤ 5 ms | |
| `vision_fps` | ≥ 20 fps | input 30 fps 기준 |
| `drop_rate` | ≤ 30 % @ 30 fps input | latest-wins 정책상 drop은 허용 |
| CPU usage | vision ≤ 200 %, 나머지 각 ≤ 30 % | `docker stats` |
| Memory usage | vision ≤ 800 MiB, 나머지 각 ≤ 200 MiB | |
| payload size | frame ≤ 40 KB, landmarks ≤ 4 KB/hand, result ≤ 8 KB | |

## 5. 보고 형식

- 지표별 p50 / p95 / p99 / max, 평균 fps, drop_rate
- 구간별 latency stacked bar (browser→gateway, vision, gesture, canvas, gateway→browser)
- 시나리오별 비교 표
- 결과 원본은 `benchmarks/results/` (git ignore)에 저장
