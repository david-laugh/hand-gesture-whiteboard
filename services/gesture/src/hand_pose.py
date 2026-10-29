"""손 모양 판별 (순수 Python, FastAPI와 무관).

y좌표 비교 대신 "손목에서 얼마나 먼가"로 판단한다.
손등을 찍고, 손 크기가 계속 바뀌고, 좌우 반전도 없기 때문에 방향에 상관없는 거리 기반이 안전하다.

포즈
- DRAW:  검지만 폄 (엄지는 검지 옆)
- ZOOM:  엄지·검지만 씀, 나머지 세 손가락 접음. 집게(엄지 끝 4 ↔ 검지 끝 8 붙임) 또는 L자(엄지를 검지에서 벌림)
- ERASE: 다섯 손끝 모으기. 네 손가락이 "접힘"으로 보이지만 손끝이 손목에서 멂 (tip_wrist 큼)
- FIST:  주먹. 네 손가락 접힘 + 손끝이 손목에 가까움 (tip_wrist 작음)
- V:     검지 + 중지
- PALM:  네 손가락 모두 폄
- NONE:  그 밖 (주먹과 모으기 사이의 애매한 값 포함)
"""
import math
from dataclasses import dataclass
from typing import Protocol, Sequence

from . import config

WRIST = 0
THUMB_TIP = 4
INDEX_MCP = 5
MIDDLE_MCP = 9  # 중지 시작 마디. 손목~이 점 거리를 "손 크기"로 쓴다.
PINKY_MCP = 17
INDEX_TIP = 8

# 손가락 이름: (손끝, 둘째 마디)
INDEX = (8, 6)
MIDDLE = (12, 10)
RING = (16, 14)
PINKY = (20, 18)
FINGERS = (INDEX, MIDDLE, RING, PINKY)
FINGER_TIPS = (8, 12, 16, 20)
ALL_TIPS = (4, 8, 12, 16, 20)

DRAW, ERASE, ZOOM, V, PALM, FIST, NONE = "DRAW", "ERASE", "ZOOM", "V", "PALM", "FIST", "NONE"
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


def _mean(landmarks: Sequence[Point], ids) -> Point:
    return _P(sum(landmarks[i].x for i in ids) / len(ids), sum(landmarks[i].y for i in ids) / len(ids))


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


def finger_pose(landmarks: Sequence[Point], ratio: float = config.FINGER_EXTENDED_RATIO) -> str:
    """네 손가락 펴짐 모양만으로 DRAW / V / PALM / FIST / NONE (엄지·tip_wrist는 보지 않음)."""
    if _hand_size(landmarks) <= 1e-6:
        return NONE
    return _PATTERNS.get(tuple(finger_extended(landmarks, f, ratio) for f in FINGERS), NONE)


def is_draw_pose(landmarks: Sequence[Point], ratio: float = config.FINGER_EXTENDED_RATIO) -> bool:
    """검지만 펴짐 (엄지는 무시)."""
    return finger_pose(landmarks, ratio) == DRAW


def tip_wrist(landmarks: Sequence[Point]) -> float:
    """네 손끝(8,12,16,20) 평균점 ~ 손목 거리 ÷ 손 크기. 주먹은 작고, 손끝 모으기는 크다."""
    hand = _hand_size(landmarks)
    return 0.0 if hand <= 1e-6 else _dist(_mean(landmarks, FINGER_TIPS), landmarks[WRIST]) / hand


def pinch_ratio(landmarks: Sequence[Point]) -> float:
    """엄지 끝 ~ 검지 끝 거리 ÷ 손 크기. 작을수록 붙어 있음."""
    hand = _hand_size(landmarks)
    return float("inf") if hand <= 1e-6 else _dist(landmarks[THUMB_TIP], landmarks[INDEX_TIP]) / hand


def thumb_spread(landmarks: Sequence[Point]) -> float:
    """엄지 끝 ~ 검지 시작 마디(5) 거리 ÷ 손 크기. 클수록 엄지를 벌림 (L자)."""
    hand = _hand_size(landmarks)
    return 0.0 if hand <= 1e-6 else _dist(landmarks[THUMB_TIP], landmarks[INDEX_MCP]) / hand


def thumb_index_mid(landmarks: Sequence[Point]) -> Point:
    return _mean(landmarks, (THUMB_TIP, INDEX_TIP))


@dataclass
class Thresholds:
    """상태에 따라 바뀌는 기준값 (히스테리시스). 기본값은 환경변수."""
    ratio: float = config.FINGER_EXTENDED_RATIO
    erase_enter: float = config.ERASE_TIP_WRIST_ENTER
    erase_exit: float = config.ERASE_TIP_WRIST_EXIT
    fist_max: float = config.FIST_TIP_WRIST_MAX
    zoom_pinch: float = config.ZOOM_PINCH_RATIO
    zoom_l_enter: float = config.ZOOM_L_ENTER
    zoom_l_exit: float = config.ZOOM_L_EXIT
    zoom_index_min: float = config.ZOOM_INDEX_MIN_RATIO


@dataclass
class Detection:
    pose: str  # DRAW / ERASE / ZOOM / V / PALM / FIST / NONE
    point: Point  # DRAW·그 밖: 검지 끝, ERASE: 다섯 손끝 평균, ZOOM: 엄지 끝과 검지 끝의 중간
    flip_fixed: bool  # 번호를 바꿔 다시 판별해서 나온 결과인지
    zoom_dist: float = 0.0  # ZOOM일 때 dist(4,8) ÷ 손 크기


def _three_folded(landmarks: Sequence[Point], th: Thresholds) -> bool:
    return not any(finger_extended(landmarks, f, th.ratio) for f in (MIDDLE, RING, PINKY))


def _detect(landmarks: Sequence[Point], th: Thresholds, erasing: bool, zooming: bool, zoom_cand: bool) -> Detection:
    tip = landmarks[INDEX_TIP]
    zoom = Detection(ZOOM, thumb_index_mid(landmarks), False, pinch_ratio(landmarks))
    # 줌 중에는 엄지 위치와 상관없이, 세 손가락이 접혀 있고 검지가 접히지 않았으면 계속 줌 (집게 ↔ L자 사이 포함)
    if zooming and _three_folded(landmarks, th) and finger_ratio(landmarks, INDEX) > th.zoom_index_min:
        return zoom

    pattern = finger_pose(landmarks, th.ratio)
    if pattern == FIST:
        tw = tip_wrist(landmarks)
        if tw > (th.erase_exit if erasing else th.erase_enter):
            return Detection(ERASE, _mean(landmarks, ALL_TIPS), False)
        # 집게 줌: 검지가 덜 접혀 있고 엄지 끝이 검지 끝에 붙음 (주먹은 검지가 깊게 접혀 있어 제외)
        if finger_ratio(landmarks, INDEX) > th.zoom_index_min and pinch_ratio(landmarks) < th.zoom_pinch:
            return zoom
        return Detection(FIST if tw < th.fist_max else NONE, tip, False)
    if pattern == DRAW:
        l_threshold = th.zoom_l_exit if zoom_cand else th.zoom_l_enter
        if pinch_ratio(landmarks) < th.zoom_pinch or thumb_spread(landmarks) > l_threshold:
            return zoom
        return Detection(DRAW, tip, False)
    return Detection(pattern, tip, False)


def detect_pose(
    landmarks: Sequence[Point],
    th: Thresholds | None = None,
    erasing: bool = False,
    zooming: bool = False,
    zoom_cand: bool = False,
    flip_fix: bool = config.FLIP_FIX_ENABLED,
    normal_sign: int = config.FLIP_NORMAL_SIGN,
) -> Detection:
    """원래 번호로 판별하고, 아무 포즈도 안 나오고 회전 방향이 정상과 반대일 때만 번호를 바꿔 다시 판별한다.
    erasing / zooming / zoom_cand는 히스테리시스용 (지금 지우는 중 / 줌 중 / 줌 자세가 이어지는 중)."""
    th = th or Thresholds()
    found = _detect(landmarks, th, erasing, zooming, zoom_cand)
    if found.pose != NONE or not flip_fix or chirality(landmarks) != -normal_sign:
        return found
    fixed = _detect(mirrored(landmarks), th, erasing, zooming, zoom_cand)
    if fixed.pose == NONE:
        return found
    fixed.flip_fixed = True
    return fixed


# --- 손가락 번호 뒤집힘 보정 -------------------------------------------------
# 손등을 찍으면 MediaPipe가 가끔 손가락 번호를 좌우로 뒤집어 붙인다 (검지 ↔ 새끼, 중지 ↔ 약지).
# 실제 녹화(rec01)에서 손이 잡힌 프레임의 약 30%가 이랬다. handedness는 쓰지 않고
# 손목·검지 시작 마디·새끼 시작 마디의 회전 방향(외적 부호)으로 알아낸다.
# 이 부호는 화면 안에서 손이 돌아가도 바뀌지 않고, 번호가 좌우로 뒤집힐 때만 바뀐다.

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
    """네 손가락 모양만 볼 때의 그리기 판별: (그리기 자세인지, 검지 끝 점, 뒤집힘 보정을 썼는지). 분석·회귀 테스트용."""
    found = finger_pose(landmarks, ratio)
    if found == DRAW:
        return True, landmarks[INDEX_TIP], False
    if found == NONE and flip_fix and chirality(landmarks) == -normal_sign:
        fixed = mirrored(landmarks)
        if finger_pose(fixed, ratio) == DRAW:
            return True, fixed[INDEX_TIP], True
    return False, landmarks[INDEX_TIP], False
