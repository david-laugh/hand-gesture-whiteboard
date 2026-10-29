"""환경변수 설정. 값이 없으면 기본값을 쓴다."""
import os


def _int(name: str, default: int) -> int:
    return int(os.getenv(name, default))


def _float(name: str, default: float) -> float:
    return float(os.getenv(name, default))


SERVICE_NAME = os.getenv("SERVICE_NAME", "gesture")
SCHEMA_VERSION = os.getenv("SCHEMA_VERSION", "1.0.0")
SCHEMA_MAJOR = SCHEMA_VERSION.split(".")[0]

# 그리기 자세가 몇 프레임 연속이면 DRAW_START (N)
DRAW_ENTER_FRAMES = _int("DRAW_ENTER_FRAMES", 3)
# 그리기 자세가 아닌 프레임이 몇 번 연속이면 DRAW_END (M)
DRAW_EXIT_FRAMES = _int("DRAW_EXIT_FRAMES", 3)
# 이전 프레임과 간격이 이보다 길면 세션 상태 초기화 (ms)
SESSION_GAP_MS = _int("SESSION_GAP_MS", 500)

# 손가락 "펴짐" 기준: (손목~손끝 − 손목~둘째 마디) ÷ 손 크기 > 이 값
FINGER_EXTENDED_RATIO = _float("FINGER_EXTENDED_RATIO", 0.15)

# One-Euro 필터 (pointer 떨림 줄이기). 실제 손으로 튜닝 필요.
ONE_EURO_MIN_CUTOFF = _float("ONE_EURO_MIN_CUTOFF", 1.0)  # 낮을수록 천천히 움직일 때 더 부드러움
ONE_EURO_BETA = _float("ONE_EURO_BETA", 10.0)  # 높을수록 빠르게 움직일 때 덜 늦게 따라옴
ONE_EURO_D_CUTOFF = _float("ONE_EURO_D_CUTOFF", 1.0)
