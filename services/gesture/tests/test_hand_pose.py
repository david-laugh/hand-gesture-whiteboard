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


@pytest.mark.parametrize("scale", [0.5, 2.0])
def test_pose_does_not_depend_on_hand_size(scale):
    """손이 카메라에 가까워지거나 멀어져도(크기가 바뀌어도) 판별이 같아야 한다."""
    from types import SimpleNamespace

    lms = load_landmarks("index_pose")
    wx, wy = lms[0].x, lms[0].y
    scaled = [SimpleNamespace(x=wx + (p.x - wx) * scale, y=wy + (p.y - wy) * scale) for p in lms]
    assert is_draw_pose(scaled)
