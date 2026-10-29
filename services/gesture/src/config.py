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
# 처리 시각 − 입력 시각이 이보다 크면 경고 로그 (ms)
LAG_WARN_MS = _int("LAG_WARN_MS", 200)

# 손가락 "펴짐" 기준: (손목~손끝 − 손목~둘째 마디) ÷ 손 크기 > 이 값
FINGER_EXTENDED_RATIO = _float("FINGER_EXTENDED_RATIO", 0.15)

# 손가락 번호 뒤집힘 보정 (hand_pose.classify_draw). handedness는 쓰지 않는다.
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
WS_INPUT_ENABLED = _bool("WS_INPUT_ENABLED", True)  # ws://<host>:8002/ws/landmarks
MQTT_HOST = os.getenv("MQTT_HOST", "hgw-mqtt")
MQTT_PORT = _int("MQTT_PORT", 1883)
MQTT_TOPIC_PREFIX = os.getenv("MQTT_TOPIC_PREFIX", "hgw/v1").strip("/")
if not MQTT_TOPIC_PREFIX or any(c in MQTT_TOPIC_PREFIX for c in "+#\0"):
    raise ValueError("Invalid MQTT_TOPIC_PREFIX")
