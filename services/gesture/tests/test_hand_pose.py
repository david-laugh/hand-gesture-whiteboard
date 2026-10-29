import pytest

from src.hand_pose import INDEX, MIDDLE, PINKY, RING, finger_extended, is_draw_pose
from tests.conftest import load_landmarks


@pytest.mark.parametrize(
    "name, expected",
    [("index_pose", True), ("fist", False), ("open_palm", False)],
)
def test_only_index_pose_is_draw_pose(name, expected):
    assert is_draw_pose(load_landmarks(name)) is expected


def test_finger_states_of_open_palm():
    lms = load_landmarks("open_palm")
    assert all(finger_extended(lms, f) for f in (INDEX, MIDDLE, RING, PINKY))


def test_finger_states_of_fist():
    lms = load_landmarks("fist")
    assert not any(finger_extended(lms, f) for f in (INDEX, MIDDLE, RING, PINKY))


def test_flipped_labels_are_fixed_and_pointer_is_real_index_tip():
    """MediaPipe가 검지↔새끼 번호를 뒤집은 경우: 보정해서 그리기 자세로 보고, 끝점은 원래 20번 점."""
    from src.hand_pose import chirality, classify_draw, mirrored

    lms = load_landmarks("index_pose")
    flipped = mirrored(lms)  # 8번 자리에 새끼 끝, 20번 자리에 검지 끝
    assert chirality(flipped) == -chirality(lms)
    assert not is_draw_pose(flipped)

    ok, tip, fixed = classify_draw(flipped, normal_sign=chirality(lms), flip_fix=True)
    assert ok and fixed
    assert tip is flipped[20]
    assert (tip.x, tip.y) == (lms[8].x, lms[8].y)  # 실제 검지 끝
    assert (tip.x, tip.y) != (flipped[8].x, flipped[8].y)  # 8번(실제 새끼 끝)이 아님


def test_flip_fix_only_when_chirality_is_reversed():
    from src.hand_pose import chirality, classify_draw, mirrored

    lms = load_landmarks("index_pose")
    flipped = mirrored(lms)
    # 회전 방향이 "정상"이라고 설정되어 있으면 보정하지 않는다
    assert classify_draw(flipped, normal_sign=chirality(flipped), flip_fix=True)[0] is False
    # 보정을 끄면 하지 않는다
    assert classify_draw(flipped, normal_sign=chirality(lms), flip_fix=False)[0] is False
    # 원래 그리기 자세면 보정 표시 없음
    assert classify_draw(lms, normal_sign=-chirality(lms), flip_fix=True) == (True, lms[8], False)


def test_flip_fixed_draw_through_state_machine_points_at_index_tip():
    from src.hand_pose import chirality, mirrored
    from src.state_machine import GestureStateMachine

    lms = load_landmarks("index_pose")
    flipped = mirrored(lms)
    m = GestureStateMachine(enter_frames=3, exit_frames=5, flip_fix=True, normal_sign=chirality(lms))
    results = [m.step(flipped, 1000 + i * 33, 1000 + i * 33) for i in range(3)]
    start = results[-1]
    assert start.gesture == "DRAW_START" and start.flip_fixed
    assert start.pointer == pytest.approx((lms[8].x, lms[8].y))
    assert start.pointer != pytest.approx((flipped[8].x, flipped[8].y))


@pytest.mark.parametrize("scale", [0.5, 2.0])
def test_pose_does_not_depend_on_hand_size(scale):
    """손이 카메라에 가까워지거나 멀어져도(크기가 바뀌어도) 판별이 같아야 한다."""
    from types import SimpleNamespace

    lms = load_landmarks("index_pose")
    wx, wy = lms[0].x, lms[0].y
    scaled = [SimpleNamespace(x=wx + (p.x - wx) * scale, y=wy + (p.y - wy) * scale) for p in lms]
    assert is_draw_pose(scaled)
