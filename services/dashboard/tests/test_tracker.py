"""Unit tests for frame correlation and latency math (stdlib unittest, no broker needed).

    cd services/dashboard && python -m unittest discover -s tests -v
"""

import json
import struct
import unittest

from src.feeds import parse_payload
from src.tracker import LatencyTracker, percentile, summarize

T0 = 1_793_000_000_000.0


def frame(sid="s1", fid=0, captured=T0, received=T0 + 10):
    return {"session_id": sid, "frame_id": fid, "captured_at_ms": captured, "received_at_ms": received}


def msg(sid="s1", fid=0, **extra):
    return {"session_id": sid, "frame_id": fid, **extra}


def run_full(tracker, fid, base, stage_ms=(10, 3, 8, 1, 2), sid="s1"):
    """uplink, gateway, vision, gesture, canvas durations → feed one complete frame."""
    up, gw, vi, ge, ca = stage_ms
    t = base
    tracker.ingest("frames", frame(sid, fid, captured=t, received=t + up), t + up + gw)
    t += up + gw
    tracker.ingest("landmarks", msg(sid, fid, hands=[{}]), t + vi)
    t += vi
    tracker.ingest("gestures", msg(sid, fid, gesture="DRAW_MOVE", gesture_ms=0.1), t + ge)
    t += ge
    tracker.ingest("canvas", msg(sid, fid, canvas_ms=0.01), t + ca)
    return t + ca


class TrackerTest(unittest.TestCase):
    def test_complete_frame_stage_latencies(self):
        tr = LatencyTracker()
        end = run_full(tr, 1, T0)
        snap = tr.snapshot(end + 1)
        st = snap["stages"]
        self.assertEqual((st["uplink"]["p50"], st["gateway"]["p50"], st["vision"]["p50"],
                          st["gesture"]["p50"], st["canvas"]["p50"]), (10, 3, 8, 1, 2))
        self.assertEqual(st["e2e"]["p50"], 24)
        self.assertEqual(st["server"]["p50"], 14)
        self.assertEqual(snap["outcomes"]["complete"], 1)
        self.assertEqual(snap["recent"][0]["gesture"], "DRAW_MOVE")
        self.assertEqual(snap["open_frames"], 0)

    def test_frames_are_joined_by_session_and_frame_id(self):
        tr = LatencyTracker()
        tr.ingest("frames", frame("a", 5), T0 + 20)
        tr.ingest("frames", frame("b", 5), T0 + 21)
        tr.ingest("landmarks", msg("b", 5, hands=[]), T0 + 40)
        tr.ingest("landmarks", msg("a", 5, hands=[{}]), T0 + 30)
        rows = {r["session_id"]: r for r in tr.snapshot(T0 + 5000)["recent"]}
        self.assertEqual(rows["a"]["stages"]["vision"], 10)
        self.assertEqual(rows["b"]["stages"]["vision"], 19)
        self.assertIs(rows["b"]["hand"], False)

    def test_incomplete_frames_are_classified_after_settle(self):
        tr = LatencyTracker(settle_ms=2000)
        tr.ingest("frames", frame(fid=1), T0)                          # vision dropped it
        tr.ingest("frames", frame(fid=2), T0)
        tr.ingest("landmarks", msg(fid=2, hands=[]), T0 + 5)           # no gesture
        tr.ingest("frames", frame(fid=3), T0)
        tr.ingest("landmarks", msg(fid=3, hands=[{}]), T0 + 5)
        tr.ingest("gestures", msg(fid=3, gesture="IDLE"), T0 + 6)      # no canvas
        self.assertEqual(tr.snapshot(T0 + 1000)["open_frames"], 3)     # still waiting
        snap = tr.snapshot(T0 + 2500)
        self.assertEqual(snap["outcomes"], {"complete": 0, "vision_dropped": 1, "no_gesture": 1, "no_canvas": 1})
        self.assertAlmostEqual(snap["rates"]["vision_drop"], 1 / 3, places=3)

    def test_messages_without_a_camera_frame_are_unmatched(self):
        tr = LatencyTracker()
        tr.ingest("gestures", msg("ui-session", 0, gesture="DRAW_START"), T0)   # canvas mouse UI
        tr.ingest("landmarks", {"frame_id": 1}, T0)                              # malformed: ignored
        snap = tr.snapshot(T0 + 10)
        self.assertEqual(snap["unmatched"]["gestures"], 1)
        self.assertEqual(snap["frames_in_window"], 0)

    def test_window_and_history(self):
        tr = LatencyTracker()
        for i in range(30):                                  # 30 frames over 1 s
            run_full(tr, i, T0 + i * 33, stage_ms=(10, 3, 8 + i % 3, 1, 2))
        now = T0 + 1500
        snap = tr.snapshot(now, window_s=30)
        self.assertEqual(snap["frames_in_window"], 30)
        self.assertEqual(snap["stages"]["vision"]["max"], 10)
        hist = snap["history"]
        self.assertEqual(len(hist), 120)
        self.assertEqual(sum(h["frames"] for h in hist), 30)
        self.assertTrue(all(h["e2e_p50"] is not None for h in hist if h["frames"]))
        later = tr.snapshot(now + 31_000, window_s=30)       # outside the 30 s window
        self.assertEqual(later["frames_in_window"], 0)

    def test_fps_per_topic(self):
        tr = LatencyTracker()
        for i in range(31):
            tr.ingest("frames", frame(fid=i), T0 + i * 100)  # 10 FPS
        self.assertEqual(tr.snapshot(T0 + 3000)["fps"]["frames"], 10.0)

    def test_current_session_follows_latest_frames(self):
        tr = LatencyTracker()
        tr.ingest("frames", frame("old", 0), T0)
        tr.ingest("frames", frame("new", 0), T0 + 100)
        snap = tr.snapshot(T0 + 600)
        self.assertEqual(snap["current_session"], "new")
        self.assertEqual(snap["current_session_idle_ms"], 500)


class HelpersTest(unittest.TestCase):
    def test_percentile_and_summary(self):
        self.assertIsNone(percentile([], 50))
        self.assertEqual(percentile([1, 2, 3, 4], 50), 2)
        self.assertEqual(summarize([3, 1, 2])["p95"], 3)
        self.assertEqual(summarize([])["n"], 0)

    def test_parse_frames_packet_keeps_only_metadata(self):
        meta = {"session_id": "s", "frame_id": 1, "captured_at_ms": 1, "received_at_ms": 2}
        raw = json.dumps(meta).encode()
        packet = struct.pack(">I", len(raw)) + raw + b"\xff\xd8\xff" + b"\x00" * 100
        self.assertEqual(parse_payload("frames", packet), meta)
        self.assertIsNone(parse_payload("frames", b"\x00\x00\x00\x09{}"))
        self.assertEqual(parse_payload("gestures", b'{"a": 1}'), {"a": 1})
        self.assertIsNone(parse_payload("canvas", b"not json"))


if __name__ == "__main__":
    unittest.main()
