"""손 모양 판별 (순수 Python, FastAPI와 무관).

y좌표 비교 대신 "손목에서 얼마나 먼가"로 판단한다.
손등을 찍고, 손 크기가 계속 바뀌고, 좌우 반전도 없기 때문에 방향에 상관없는 거리 기반이 안전하다.
"""
import math
from typing import Protocol, Sequence

from . import config

WRIST = 0
MIDDLE_MCP = 9  # 중지 시작 마디. 손목~이 점 거리를 "손 크기"로 쓴다.
INDEX_TIP = 8

# 손가락 이름: (손끝, 둘째 마디)
INDEX = (8, 6)
MIDDLE = (12, 10)
RING = (16, 14)
PINKY = (20, 18)


class Point(Protocol):
    x: float
    y: float


def _dist(a: Point, b: Point) -> float:
    return math.hypot(a.x - b.x, a.y - b.y)


def finger_extended(
    landmarks: Sequence[Point],
    finger: tuple[int, int],
    ratio: float = config.FINGER_EXTENDED_RATIO,
) -> bool:
    """손끝이 손목에서 둘째 마디보다 (손 크기 × ratio) 이상 멀면 펴진 것."""
    wrist = landmarks[WRIST]
    hand_size = _dist(wrist, landmarks[MIDDLE_MCP])
    if hand_size <= 1e-6:
        return False
    tip, pip = finger
    gap = _dist(wrist, landmarks[tip]) - _dist(wrist, landmarks[pip])
    return gap / hand_size > ratio


def is_draw_pose(landmarks: Sequence[Point], ratio: float = config.FINGER_EXTENDED_RATIO) -> bool:
    """검지만 펴짐 (엄지는 무시)."""
    return (
        finger_extended(landmarks, INDEX, ratio)
        and not finger_extended(landmarks, MIDDLE, ratio)
        and not finger_extended(landmarks, RING, ratio)
        and not finger_extended(landmarks, PINKY, ratio)
    )


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


def classify_draw(
    landmarks: Sequence[Point],
    ratio: float = config.FINGER_EXTENDED_RATIO,
    flip_fix: bool = config.FLIP_FIX_ENABLED,
    normal_sign: int = config.FLIP_NORMAL_SIGN,
) -> tuple[bool, Point, bool]:
    """(그리기 자세인지, 검지 끝 점, 뒤집힘 보정을 썼는지).

    회전 방향이 정상값과 반대이고 원래 번호로는 그리기 자세가 아닐 때만 번호를 바꿔 다시 판별한다.
    보정으로 그리기 자세가 되면 검지 끝은 원래 번호 20번 점이다 (8번은 실제로 새끼 끝).
    """
    if is_draw_pose(landmarks, ratio):
        return True, landmarks[INDEX_TIP], False
    if flip_fix and chirality(landmarks) == -normal_sign:
        fixed = mirrored(landmarks)
        if is_draw_pose(fixed, ratio):
            return True, fixed[INDEX_TIP], True
    return False, landmarks[INDEX_TIP], False
