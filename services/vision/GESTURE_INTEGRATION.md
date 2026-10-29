# Vision → Gesture 연동 가이드 (Gesture 담당자용)

Vision(B)은 손 landmark를 **MQTT로 발행**한다. Gesture(C)는 같은 브로커를 **구독**하면 된다.
서버를 열 필요가 없고, Vision이 떠 있는지 신경 쓸 필요도 없다.

```text
Gateway ─ hgw/v1/frames/{session_id} ─► Vision ─ hgw/v1/landmarks/{session_id} ─► Gesture ─ hgw/v1/gestures/{session_id} ─► Canvas
```

## 1. 구독 정보

| 항목 | 값 |
|---|---|
| Broker | Docker 안: `hgw-mqtt:1883` / 호스트: `localhost:11883` (gateway README와 동일) |
| Topic (구독) | **`hgw/v1/landmarks/+`** (`hgw/v1` = `MQTT_TOPIC_PREFIX`) |
| Topic (개별) | `hgw/v1/landmarks/{session_id}` — 끝부분은 payload의 `session_id`와 항상 같음 |
| QoS / retain | 0 / false |
| Payload | UTF-8 JSON 1개 = 카메라 프레임 1개 |
| 빈도 | 실제로 추론한 프레임마다 1개 (최대 카메라 FPS, 기본 약 30) |

## 2. 메시지 형식

```json
{
  "type": "landmarks",
  "session_id": "142de33c65fc4ee3b400f754c093b973",
  "frame_id": 57,
  "ts": 1793241731920,
  "hands": [
    {
      "handedness": "Right",
      "score": 0.95,
      "landmarks": [[0.512, 0.781, 0.0], [0.448, 0.742, -0.021], "… 정확히 21개 [x, y, z]"]
    }
  ],
  "gesture": null
}
```

| 필드 | 타입 | 의미 |
|---|---|---|
| `type` | `"landmarks"` | 고정 |
| `session_id` | string | 카메라 세션 ID (gateway가 촬영 시작마다 발급하는 32자 hex). topic 끝과 같음 |
| `frame_id` | int ≥ 0 | 세션 안의 프레임 번호 (gateway 발급, 증가). **건너뛸 수 있음** (§3) |
| `ts` | int (epoch ms) | **브라우저가 프레임을 캡처한 시각** (gateway `captured_at_ms`). 속도 계산·debounce에 사용 |
| `hands` | array, 0~1개 | 손이 없으면 `[]` |
| `hands[].handedness` | `"Left"` \| `"Right"` | MediaPipe 판정. 손등 촬영 시 반대로 나올 수 있음 (§4) |
| `hands[].score` | 0~1 | **handedness 분류 점수** (손 검출 신뢰도가 아님) |
| `hands[].landmarks` | 21 × `[x, y, z]` | MediaPipe 순서. `x`,`y`는 이미지 기준 정규화 좌표, `z`는 손목 기준 상대 깊이(작을수록 카메라에 가까움) |
| `gesture` | `null` | 항상 null. 판정은 Gesture가 한다 |

Landmark 번호: 0 WRIST · 1–4 THUMB(4=TIP) · 5–8 INDEX(8=TIP) · 9–12 MIDDLE(12=TIP) · 13–16 RING(16=TIP) · 17–20 PINKY(20=TIP)

## 3. 반드시 알아야 할 동작

- **손 없음 = `hands: []` 메시지가 온다.** 메시지가 아예 안 오는 것은 손이 없는 게 아니라 카메라/네트워크가 멈춘 것이다. 손 없음과 입력 끊김은 구분해서 처리한다 (예: N ms 동안 메시지가 없으면 진행 중인 stroke를 `*_END`).
- **frame_id는 건너뛸 수 있다.** Vision이 처리 속도를 넘는 프레임을 버리기 때문이다 (latest-wins). 버린 프레임은 메시지를 보내지 않는다. 연속된 frame_id를 가정하지 말고 `ts` 차이로 시간 간격을 계산한다.
- **x, y는 0~1로 clamp하지 않는다.** 손이 화면 밖에 걸치면 0 미만이나 1 초과가 나올 수 있다. Canvas의 `pointer`는 0~1이어야 하므로 Gesture에서 clamp한다.
- **z는 정규화하지 않는다.** 상대값이라 절대 거리로 쓰지 않는다. 손 크기 기준 비율(예: 손목–중지 MCP 거리)로 판정하는 것이 안정적이다.
- **좌우 반전 없음.** 이미지 그대로의 좌표다 (gateway 카메라는 후면 카메라, 반전 없음). 기본 해상도 960 기준이지만 좌표는 정규화되어 있어 해상도와 무관하다.
- **세션별 상태.** `session_id`마다 state machine을 따로 둔다. 촬영을 멈췄다 다시 시작하면 새 `session_id`가 온다.
- **QoS 0.** 브로커/네트워크 문제로 메시지가 빠질 수 있고, Gesture가 구독하기 전 메시지는 사라진다(retain false). 위의 "건너뛸 수 있음"과 같은 방식으로 처리하면 된다.
- Gesture가 Canvas로 보내는 topic(`hgw/v1/gestures/{session_id}`)에 같은 `session_id`를 쓰면 프레임 단위로 추적할 수 있다. Canvas 화면은 `?session_id=` 값이 같아야 그 그림을 보여 준다.

## 4. Handedness 주의

프로젝트는 손등이 카메라를 향하는 촬영을 가정한다. MediaPipe의 handedness는 이 조건에서 **실제 손과 반대로 나올 수 있고, 아직 실제 카메라로 검증하지 않았다.**
판정 로직이 왼손/오른손에 의존하지 않도록 만들거나, 의존해야 한다면 Vision 담당자에게 알려 달라 (Vision에서 `VISION_SWAP_HANDEDNESS=true`로 뒤집을 수 있다).

## 5. 최소 구독 코드 (paho-mqtt 2.x)

```python
import json
import paho.mqtt.client as mqtt

def on_connect(client, userdata, flags, reason, properties):
    client.subscribe("hgw/v1/landmarks/+", qos=0)   # 재연결 때마다 다시 구독

def on_message(client, userdata, msg):
    m = json.loads(msg.payload)
    session_id, frame_id, ts = m["session_id"], m["frame_id"], m["ts"]
    if not m["hands"]:
        ...  # 손 없음
        return
    hand = m["hands"][0]
    index_tip_x, index_tip_y, _ = hand["landmarks"][8]
    ...  # state machine → hgw/v1/gestures/{session_id} 발행

client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
client.on_connect = on_connect
client.on_message = on_message
client.connect("hgw-mqtt", 1883)        # 호스트에서 실행하면 "localhost", 11883
client.loop_forever()
```

## 6. 테스트 데이터

| 방법 | 명령 (`services/vision`에서) | 내용 |
|---|---|---|
| 정적 샘플 | `samples/detected_hand.json`, `no_hand.json`, `continuous_frames.json` | 실제 추론 결과이지만 정지 사진에서 만든 것 (손 모양 변화 없음) |
| 실제 녹화 재생 | `python -m tools.replay_landmarks samples/recorded/<동작>.jsonl --loop` | 녹화된 실제 카메라 메시지를 원래 시간 간격대로 `hgw/v1/landmarks/{session_id}`로 발행 |
| 실시간 | E2E 환경 실행 후 `http://localhost:8010/cam` 촬영 ([`e2e/README.md`](e2e/README.md)) | 카메라 → gateway → vision → 실제 메시지 |
| 메시지 확인 | `python -m tests.landmark_recorder --host localhost --port 11883` | 들어오는 메시지를 계약 검증하며 출력 |

`replay_landmarks` 옵션: `--speed 0.5`(느리게), `--session-id my-test`(session 고정), `--rebase-ts`(ts를 현재 시각 기준으로), `--broker host:port`.

메시지 형식 검증 함수: `services/vision/src/contract.py`의 `validate_landmarks_message()`.
