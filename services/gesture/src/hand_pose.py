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
