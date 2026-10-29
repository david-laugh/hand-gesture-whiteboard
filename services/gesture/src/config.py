"""환경변수 설정. 값이 없으면 기본값을 쓴다."""
import os


def _int(name: str, default: int) -> int:
    return int(os.getenv(name, default))


def _float(name: str, default: float) -> float:
    return float(os.getenv(name, default))


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


SERVICE_NAME = os.getenv("SERVICE_NAME", "gesture")
SCHEMA_VERSION = os.getenv("SCHEMA_VERSION", "1.0.0")
SCHEMA_MAJOR = SCHEMA_VERSION.split(".")[0]

# 그리기 자세가 몇 프레임 연속이면 DRAW_START (N)
DRAW_ENTER_FRAMES = _int("DRAW_ENTER_FRAMES", 3)
# 그리기 자세가 아닌 프레임이 몇 번 연속이면 DRAW_END (M).
# 실제 녹화(30fps)에서 3~4프레임짜리 튐이 획을 끊어서 5로 정함 (30fps에서 약 167ms).
DRAW_EXIT_FRAMES = _int("DRAW_EXIT_FRAMES", 5)
# 이전 프레임과 간격이 이보다 길면 세션 상태 초기화 (ms)
SESSION_GAP_MS = _int("SESSION_GAP_MS", 500)
# 메시지가 이만큼 없던 세션은 메모리에서 지운다 (펜 색·굵기 설정도 함께 사라짐)
SESSION_TTL_S = _float("SESSION_TTL_S", 600)
# 처리 시각 − 입력 시각이 이보다 크면 경고 로그 (ms)
LAG_WARN_MS = _int("LAG_WARN_MS", 200)

# 손가락 "펴짐" 기준: (손목~손끝 − 손목~둘째 마디) ÷ 손 크기 > 이 값
FINGER_EXTENDED_RATIO = _float("FINGER_EXTENDED_RATIO", 0.15)

# tip_wrist = 네 손끝(8,12,16,20) 평균 ~ 손목 거리 ÷ 손 크기. 네 손가락이 "접힘"으로 보일 때만 쓴다.
# 녹화 rec03: 주먹 0.78~1.16, 다섯 손끝 모으기 1.21~1.60
ERASE_TIP_WRIST_ENTER = _float("ERASE_TIP_WRIST_ENTER", 1.20)  # 손끝 모으기(지우개) 진입
ERASE_TIP_WRIST_EXIT = _float("ERASE_TIP_WRIST_EXIT", 1.15)  # 지우는 중 유지 (히스테리시스)
FIST_TIP_WRIST_MAX = _float("FIST_TIP_WRIST_MAX", 1.18)  # 이보다 작아야 주먹. 그 사이는 포즈 없음

# 줌 (엄지·검지, 나머지 세 손가락 접음)
ZOOM_PINCH_RATIO = _float("ZOOM_PINCH_RATIO", 0.20)  # 집게: dist(4,8) ÷ 손 크기가 이보다 작음 (rec01 그리기 최소 0.28)
# L자: 엄지 끝 ~ 검지 시작 마디(5) ÷ 손 크기. rec01 그리기 중 최대 1.37이라 그보다 높게.
ZOOM_L_ENTER = _float("ZOOM_L_ENTER", 1.45)
ZOOM_L_EXIT = _float("ZOOM_L_EXIT", 1.30)  # L자가 이어지는 중에는 이 값까지 L자로 봄
ZOOM_INDEX_MIN_RATIO = _float("ZOOM_INDEX_MIN_RATIO", 0.0)  # 검지가 이보다 접히면 줌 아님 (주먹 구분)
# 검지를 둥글게 말아 엄지에 꼭 붙인 집게 (녹화 rec04: 거리 중앙값 0.05, 검지 중앙값 −0.12).
# 진짜 주먹은 거리 0.12 이상·검지 −0.20 이하라서 겹치지 않는다.
ZOOM_PINCH_TIGHT = _float("ZOOM_PINCH_TIGHT", 0.10)
ZOOM_TIGHT_INDEX_MIN = _float("ZOOM_TIGHT_INDEX_MIN", -0.20)
# 줌 중에는 세 손가락이 접혀 있고 (검지 > 이 값 또는 꼭 붙인 집게)이면 엄지·검지 거리와 상관없이 계속 줌
ZOOM_KEEP_INDEX_MIN = _float("ZOOM_KEEP_INDEX_MIN", -0.20)
ZOOM_SCALE_MIN = _float("ZOOM_SCALE_MIN", 0.5)
ZOOM_SCALE_MAX = _float("ZOOM_SCALE_MAX", 3.0)
# 배율 = 시작 배율 × 현재 거리 ÷ 시작 거리. 거리가 이보다 작으면 이 값으로 계산 (집게에서 시작할 때 너무 민감하지 않게)
ZOOM_DIST_FLOOR = _float("ZOOM_DIST_FLOOR", 0.25)

# 일회성 명령 (주먹=UNDO, 브이=펜 색, 손바닥=굵기): 이만큼 유지하면 1번 발행
COMMAND_HOLD_MS = _int("COMMAND_HOLD_MS", 500)
# 주먹(UNDO)만 더 길게: 획을 끝내며 손을 접는 동작과 구분하기 위해. 획이 끝난 시점부터 잰다.
UNDO_HOLD_MS = _int("UNDO_HOLD_MS", 700)
# 일회성 명령 사이 최소 간격
COMMAND_COOLDOWN_MS = _int("COMMAND_COOLDOWN_MS", 1000)

# 펜 색 순환 (canvas 그림판 팔레트와 같은 순서). 첫 번째가 기본색.
PEN_COLORS = [c.strip() for c in os.getenv(
    "PEN_COLORS", "#172554,#dc2626,#ea580c,#ca8a04,#16a34a,#0891b2,#7c3aed,#111827").split(",") if c.strip()]
# 굵기 순환 (px). 기본값은 *_DEFAULT_PX, 손바닥을 할 때마다 목록의 다음 값.
BRUSH_SIZES_PX = [float(v) for v in os.getenv("BRUSH_SIZES_PX", "4,8,16").split(",")]
BRUSH_DEFAULT_PX = _float("BRUSH_DEFAULT_PX", 8)
ERASER_SIZES_PX = [float(v) for v in os.getenv("ERASER_SIZES_PX", "20,30,50").split(",")]  # 지우개 지름
ERASER_DEFAULT_PX = _float("ERASER_DEFAULT_PX", 30)
# canvas는 eraser_radius(정규화) × min(가로, 세로) × 2 를 지우개 지름(px)으로 쓴다 (기본 1280×720 → 720)
CANVAS_MIN_SIDE_PX = _float("CANVAS_MIN_SIDE_PX", 720)

# 손가락 번호 뒤집힘 보정 (hand_pose.detect_pose). handedness는 쓰지 않는다.
FLIP_FIX_ENABLED = _bool("FLIP_FIX_ENABLED", True)
# 이 촬영 환경(오른손 손등, 후면 카메라, 좌우 반전 없음)에서 정상인 회전 방향. rec01 녹화 기준 +1.
FLIP_NORMAL_SIGN = _int("FLIP_NORMAL_SIGN", 1)
if FLIP_NORMAL_SIGN not in (1, -1):
    raise ValueError("FLIP_NORMAL_SIGN must be 1 or -1")

# One-Euro 필터 (pointer 떨림 줄이기). 실제 손으로 튜닝 필요.
ONE_EURO_MIN_CUTOFF = _float("ONE_EURO_MIN_CUTOFF", 1.0)  # 낮을수록 천천히 움직일 때 더 부드러움
ONE_EURO_BETA = _float("ONE_EURO_BETA", 10.0)  # 높을수록 빠르게 움직일 때 덜 늦게 따라옴
ONE_EURO_D_CUTOFF = _float("ONE_EURO_D_CUTOFF", 1.0)

# MQTT (gateway와 같은 방식). MQTT_ENABLED=false면 MQTT 입력과 발행이 모두 꺼진다.
MQTT_ENABLED = _bool("MQTT_ENABLED", True)
# 입력 방식. 둘 다 켜 두면 vision이 어느 쪽으로 보내도 받는다.
MQTT_INPUT_ENABLED = _bool("MQTT_INPUT_ENABLED", True)  # {prefix}/landmarks/+ 구독
WS_INPUT_ENABLED = _bool("WS_INPUT_ENABLED", False)  # ws://<host>:8002/ws/landmarks (vision이 MQTT로 바뀌어 기본 끔)
MQTT_HOST = os.getenv("MQTT_HOST", "hgw-mqtt")
MQTT_PORT = _int("MQTT_PORT", 1883)
MQTT_TOPIC_PREFIX = os.getenv("MQTT_TOPIC_PREFIX", "hgw/v1").strip("/")
if not MQTT_TOPIC_PREFIX or any(c in MQTT_TOPIC_PREFIX for c in "+#\0"):
    raise ValueError("Invalid MQTT_TOPIC_PREFIX")
