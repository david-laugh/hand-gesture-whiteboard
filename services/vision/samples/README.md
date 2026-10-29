# Vision 출력 샘플 (실제 추론 결과)

이 디렉터리의 JSON은 **Mock이 아니라 실제 MediaPipe HandLandmarker 추론 결과**다.
각 파일은 합의된 vision → gesture 계약 형식(`type`, `session_id`, `frame_id`, `ts`, `hands`, `gesture`) 그대로이며 추가 key는 없다.

| 파일 | 내용 | 입력 프레임 |
|---|---|---|
| `detected_hand.json` | 손 1개 검출, landmark 21개 | 공식 테스트 이미지에서 잘라낸 640×480 crop (y=300..780) |
| `no_hand.json` | 손 미검출 (`hands: []`) | 같은 이미지의 빈 벽 영역 (y=0..300) → 640×480 resize |
| `continuous_frames.json` | 30개 연속 메시지 (frame_id 100..129, ts 33 ms 간격) | crop 창을 6 px씩 아래로 이동 → 화면 안에서 손이 위로 이동 |

## 출처와 한계 (정직한 표기)

- 입력 이미지: MediaPipe 공식 Hand Landmarker 예제 이미지
  `https://storage.googleapis.com/mediapipe-tasks/hand_landmarker/woman_hands.jpg`
- **실제 카메라 영상이 아니다.** `continuous_frames.json`은 정지 사진의 crop을 이동시킨 것이라
  손가락 모양은 변하지 않고 위치만 바뀐다. 손 모양 변화(gesture) 테스트에는 실제 카메라 녹화가 필요하다
  (`python -m tools.visual_check --source 0 --dump my_hand.jsonl`).
- 사진 속 손은 손바닥 쪽이 보이는 촬영이며, 프로젝트가 가정하는 "손등 촬영" 환경과 다르다.
- `ts`는 생성 시점의 epoch ms를 기준으로 33 ms 간격을 부여한 값이다.
- 모델: `hand_landmarker.task` float16 v1 (sha256 `fbc2a300…7cde1`), mediapipe 1.2.0, VIDEO mode, num_hands=1, confidence 0.5/0.5/0.5.

## 재생성

```bash
cd services/vision
python -m tools.assets             # 공식 모델 + 테스트 이미지 다운로드
python -m tools.generate_samples   # samples/*.json 재생성 (검출 실패 시 파일을 쓰지 않고 종료)
```
