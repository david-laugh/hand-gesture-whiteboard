from src import config
import pytest

IDS = ("trace_id", "session_id", "frame_id")


@pytest.mark.parametrize("name", ["index_pose", "no_hand"])
def test_fixtures_are_valid_landmark_messages(name, fixture, landmark_validator):
    landmark_validator.validate(fixture(name))


def test_healthz(client):
    res = client.get("/healthz")
    assert res.status_code == 200
    assert res.json() == {"status": "ok", "service": "gesture"}


@pytest.mark.parametrize("name", ["index_pose", "no_hand"])
def test_classify_response_passes_schema(name, client, fixture, gesture_validator):
    req = fixture(name)
    res = client.post("/v1/classify", json=req)
    assert res.status_code == 200
    body = res.json()
    gesture_validator.validate(body)
    for key in IDS:
        assert body[key] == req[key]


def test_classify_pointer_null_without_hand(client, fixture):
    body = client.post("/v1/classify", json=fixture("no_hand")).json()
    assert body["pointer"] is None


def test_draw_sequence_over_http_with_skipped_frame_ids(client, fixture, gesture_validator):
    """frame_id가 건너뛰어도 그리기가 동작하고, 모든 응답이 schema를 통과한다."""
    from tests.conftest import enter_frames_at, exit_frames_at

    n, m = enter_frames_at(33), exit_frames_at(33)
    frames = ["index_pose"] * 5 + ["fist"] * m + ["no_hand"]
    bodies = []
    for i, name in enumerate(frames):
        req = fixture(name)
        req["frame_id"] = 100 + i * 4  # 4씩 건너뜀
        req["timestamp_ms"] = 1_793_260_800_000 + i * 33
        res = client.post("/v1/classify", json=req)
        assert res.status_code == 200
        body = res.json()
        gesture_validator.validate(body)
        assert body["frame_id"] == req["frame_id"]
        bodies.append(body)

    assert [b["gesture"] for b in bodies] == (
        ["IDLE"] * (n - 1) + ["DRAW_START"] + ["DRAW_MOVE"] * (5 - n) + ["DRAW_MOVE"] * (m - 1) + ["DRAW_END", "IDLE"]
    )
    stroke_ids = {b["params"]["stroke_id"] for b in bodies[n - 1:5 + m]}
    assert len(stroke_ids) == 1
    assert "stroke_id" not in bodies[-1]["params"]
    assert bodies[-1]["pointer"] is None


def test_classify_accepts_vision_format(client, fixture, gesture_validator):
    from tests.conftest import to_vision_format

    res = client.post("/v1/classify", json=to_vision_format(fixture("index_pose")))
    assert res.status_code == 200
    body = res.json()
    gesture_validator.validate(body)
    assert body["trace_id"] == "sess-01-1042"


def test_invalid_vision_format_returns_contract_error(client, fixture):
    from tests.conftest import to_vision_format

    req = to_vision_format(fixture("index_pose"))
    req["hands"][0]["landmarks"].pop()
    res = client.post("/v1/classify", json=req)
    assert res.status_code == 422
    assert res.json()["code"] == "SCHEMA_VIOLATION"


def test_reset_clears_session_state(client, fixture):
    for i in range(4):
        req = fixture("index_pose")
        req["timestamp_ms"] += i * 33
        client.post("/v1/classify", json=req)
    assert client.post("/v1/sessions/sess-01/reset").status_code == 204
    body = client.post("/v1/classify", json=fixture("index_pose")).json()
    assert body["gesture"] == "IDLE"


def test_stroke_id_not_reused_after_reset(client, fixture):
    ts = [1_793_260_800_000]

    def post():
        req = fixture("index_pose")
        ts[0] += 33
        req["timestamp_ms"] = ts[0]
        return client.post("/v1/classify", json=req).json()

    def draw_stroke_id():
        bodies = [post() for _ in range(4)]
        start = [b for b in bodies if b["gesture"] == "DRAW_START"]
        assert len(start) == 1
        return start[0]["params"]["stroke_id"]

    first = draw_stroke_id()
    client.post("/v1/sessions/sess-01/reset")
    assert draw_stroke_id() != first


def test_reset_returns_204(client):
    res = client.post("/v1/sessions/sess-01/reset")
    assert res.status_code == 204
    assert res.content == b""


def _assert_error_body(body, code, req):
    assert body["message_type"] == "error"
    assert body["service"] == "gesture"
    assert body["code"] == code
    assert isinstance(body["message"], str) and body["message"]
    assert isinstance(body["timestamp_ms"], int)
    for key in IDS:
        assert body[key] == req[key]


def test_major_version_mismatch_returns_422(client, fixture):
    req = fixture("index_pose")
    req["schema_version"] = "2.0.0"
    res = client.post("/v1/classify", json=req)
    assert res.status_code == 422
    _assert_error_body(res.json(), "SCHEMA_VERSION_MISMATCH", req)


def test_minor_version_difference_is_accepted(client, fixture):
    req = fixture("index_pose")
    req["schema_version"] = "1.3.0"
    assert client.post("/v1/classify", json=req).status_code == 200


def test_invalid_body_returns_contract_error(client, fixture):
    req = fixture("index_pose")
    req["hands"][0]["landmarks"] = req["hands"][0]["landmarks"][:20]  # 21개가 아님
    res = client.post("/v1/classify", json=req)
    assert res.status_code == 422
    _assert_error_body(res.json(), "SCHEMA_VIOLATION", req)


def test_non_json_body_returns_contract_error(client):
    res = client.post(
        "/v1/classify",
        content=b"not json",
        headers={"Content-Type": "application/json", "X-Trace-Id": "trace-abc123"},
    )
    assert res.status_code == 422
    body = res.json()
    assert body["code"] == "SCHEMA_VIOLATION"
    assert body["trace_id"] == "trace-abc123"
