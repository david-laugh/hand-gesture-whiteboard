"""손 모양 판별 (순수 Python, FastAPI와 무관).

y좌표 비교 대신 "손목에서 얼마나 먼가"로 판단한다.
손등을 찍고, 손 크기가 계속 바뀌고, 좌우 반전도 없기 때문에 방향에 상관없는 거리 기반이 안전하다.

포즈 (엄지는 집게에만 쓴다)
- ERASE: 집게. 엄지 끝(4)과 검지 끝(8)이 붙음. 다른 포즈보다 먼저 검사
- DRAW:  검지만 폄
- V:     검지 + 중지
- PALM:  네 손가락 모두 폄
- FIST:  네 손가락 모두 접음
- NONE:  그 밖
"""
import math
from dataclasses import dataclass
from typing import Protocol, Sequence

from . import config

WRIST = 0
THUMB_TIP = 4
MIDDLE_MCP = 9  # 중지 시작 마디. 손목~이 점 거리를 "손 크기"로 쓴다.
INDEX_TIP = 8

# 손가락 이름: (손끝, 둘째 마디)
INDEX = (8, 6)
MIDDLE = (12, 10)
RING = (16, 14)
PINKY = (20, 18)
FINGERS = (INDEX, MIDDLE, RING, PINKY)

DRAW, ERASE, V, PALM, FIST, NONE = "DRAW", "ERASE", "V", "PALM", "FIST", "NONE"
# 펴진 손가락 모양 (검지, 중지, 약지, 새끼) → 포즈
_PATTERNS = {
    (True, False, False, False): DRAW,
    (True, True, False, False): V,
    (True, True, True, True): PALM,
    (False, False, False, False): FIST,
}


class Point(Protocol):
    x: float
    y: float


@dataclass
class _P:
    x: float
    y: float


def _dist(a: Point, b: Point) -> float:
    return math.hypot(a.x - b.x, a.y - b.y)


def _hand_size(landmarks: Sequence[Point]) -> float:
    return _dist(landmarks[WRIST], landmarks[MIDDLE_MCP])


def finger_ratio(landmarks: Sequence[Point], finger: tuple[int, int]) -> float:
    """(손목~손끝 − 손목~둘째 마디) ÷ 손 크기. 클수록 펴짐."""
    hand = _hand_size(landmarks)
    if hand <= 1e-6:
        return 0.0
    tip, pip = finger
    return (_dist(landmarks[WRIST], landmarks[tip]) - _dist(landmarks[WRIST], landmarks[pip])) / hand


def finger_extended(
    landmarks: Sequence[Point],
    finger: tuple[int, int],
    ratio: float = config.FINGER_EXTENDED_RATIO,
) -> bool:
    """손끝이 손목에서 둘째 마디보다 (손 크기 × ratio) 이상 멀면 펴진 것."""
    if _hand_size(landmarks) <= 1e-6:
        return False
    return finger_ratio(landmarks, finger) > ratio


def is_draw_pose(landmarks: Sequence[Point], ratio: float = config.FINGER_EXTENDED_RATIO) -> bool:
    """검지만 펴짐 (엄지는 무시)."""
    return finger_pose(landmarks, ratio) == DRAW


def finger_pose(landmarks: Sequence[Point], ratio: float = config.FINGER_EXTENDED_RATIO) -> str:
    """네 손가락 펴짐 모양으로 DRAW / V / PALM / FIST / NONE."""
    if _hand_size(landmarks) <= 1e-6:
        return NONE
    return _PATTERNS.get(tuple(finger_extended(landmarks, f, ratio) for f in FINGERS), NONE)


def pinch_ratio(landmarks: Sequence[Point]) -> float:
    """엄지 끝 ~ 검지 끝 거리 ÷ 손 크기. 작을수록 붙어 있음."""
    hand = _hand_size(landmarks)
    return float("inf") if hand <= 1e-6 else _dist(landmarks[THUMB_TIP], landmarks[INDEX_TIP]) / hand


def is_pinch(
    landmarks: Sequence[Point],
    threshold: float = config.PINCH_ENTER_RATIO,
    index_min_ratio: float = config.PINCH_INDEX_MIN_RATIO,
) -> bool:
    """집게. threshold는 지우는 중이 아니면 진입값, 지우는 중이면 이탈값을 넘긴다 (히스테리시스)."""
    # 주먹에서는 엄지가 접힌 검지 옆에 붙어 있어 거리가 가깝다 → 검지가 깊게 접혀 있으면 제외
    return pinch_ratio(landmarks) < threshold and finger_ratio(landmarks, INDEX) > index_min_ratio


def pinch_point(landmarks: Sequence[Point]) -> Point:
    a, b = landmarks[THUMB_TIP], landmarks[INDEX_TIP]
    return _P((a.x + b.x) / 2, (a.y + b.y) / 2)


# --- 손가락 번호 뒤집힘 보정 -------------------------------------------------
# 손등을 찍으면 MediaPipe가 가끔 손가락 번호를 좌우로 뒤집어 붙인다 (검지 ↔ 새끼, 중지 ↔ 약지).
# 실제 녹화(rec01)에서 손이 잡힌 프레임의 약 30%가 이랬다. handedness는 쓰지 않고
# 손목·검지 시작 마디·새끼 시작 마디의 회전 방향(외적 부호)으로 알아낸다.
# 이 부호는 화면 안에서 손이 돌아가도 바뀌지 않고, 번호가 좌우로 뒤집힐 때만 바뀐다.

INDEX_MCP, PINKY_MCP = 5, 17
# 뒤집힌 번호를 되돌리는 순서: 새 i번 점 = 원래 MIRROR[i]번 점. 엄지(1-4)와 손목(0)은 그대로.
MIRROR = list(range(5)) + list(range(17, 21)) + list(range(13, 17)) + list(range(9, 13)) + list(range(5, 9))


def chirality(landmarks: Sequence[Point]) -> int:
    """회전 방향: +1 또는 -1 (세 점이 일직선이면 0)."""
    o, a, b = landmarks[WRIST], landmarks[INDEX_MCP], landmarks[PINKY_MCP]
    cross = (a.x - o.x) * (b.y - o.y) - (a.y - o.y) * (b.x - o.x)
    return (cross > 0) - (cross < 0)


def mirrored(landmarks: Sequence[Point]) -> list[Point]:
    return [landmarks[i] for i in MIRROR]


@dataclass
class Detection:
    pose: str  # DRAW / ERASE / V / PALM / FIST / NONE
    point: Point  # DRAW·그 밖: 검지 끝, ERASE: 엄지 끝과 검지 끝의 중간
    flip_fixed: bool  # 번호를 바꿔 다시 판별해서 나온 결과인지


def _detect(landmarks: Sequence[Point], pinch_threshold: float, ratio: float) -> Detection:
    if is_pinch(landmarks, pinch_threshold):
        return Detection(ERASE, pinch_point(landmarks), False)
    return Detection(finger_pose(landmarks, ratio), landmarks[INDEX_TIP], False)


def detect_pose(
    landmarks: Sequence[Point],
    pinch_threshold: float = config.PINCH_ENTER_RATIO,
    ratio: float = config.FINGER_EXTENDED_RATIO,
    flip_fix: bool = config.FLIP_FIX_ENABLED,
    normal_sign: int = config.FLIP_NORMAL_SIGN,
) -> Detection:
    """원래 번호로 판별하고, 아무 포즈도 안 나오고 회전 방향이 정상과 반대일 때만 번호를 바꿔 다시 판별한다.
    보정으로 나온 결과의 점은 바뀐 번호 기준이다 (예: 검지 끝 = 원래 20번 점)."""
    found = _detect(landmarks, pinch_threshold, ratio)
    if found.pose != NONE or not flip_fix or chirality(landmarks) != -normal_sign:
        return found
    fixed = _detect(mirrored(landmarks), pinch_threshold, ratio)
    if fixed.pose == NONE:
        return found
    fixed.flip_fixed = True
    return fixed


def classify_draw(
    landmarks: Sequence[Point],
    ratio: float = config.FINGER_EXTENDED_RATIO,
    flip_fix: bool = config.FLIP_FIX_ENABLED,
    normal_sign: int = config.FLIP_NORMAL_SIGN,
) -> tuple[bool, Point, bool]:
    """그리기 자세만 볼 때: (그리기 자세인지, 검지 끝 점, 뒤집힘 보정을 썼는지). 집게는 보지 않는다."""
    found = finger_pose(landmarks, ratio)
    if found == DRAW:
        return True, landmarks[INDEX_TIP], False
    if found == NONE and flip_fix and chirality(landmarks) == -normal_sign:
        fixed = mirrored(landmarks)
        if finger_pose(fixed, ratio) == DRAW:
            return True, fixed[INDEX_TIP], True
    return False, landmarks[INDEX_TIP], False
