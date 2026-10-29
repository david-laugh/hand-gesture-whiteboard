"""Benchmark the vision pipeline with real inference.

    python -m tools.bench offline --frames 300
        Back-to-back decode → inference → serialize (no network). Gives pipeline capacity.

    python -m tools.bench http --url http://localhost:8001 --fps 30 --seconds 10
        POSTs frames at a fixed rate to a running service (/v1/frames) and reads its /metrics.

    python -m tools.bench mqtt --broker-host hgw-mqtt --broker-port 1883 --url http://127.0.0.1:8001
        Acts as the gateway: publishes gateway-format packets to hgw/v1/frames/{session},
        counts the landmarks vision publishes on hgw/v1/landmarks/{session}, and reads the
        running vision service's /metrics (input vs processing fps, drops, publish_ms).

Frames come from tools/frames.py (crops of the official test image) unless --video is given.
"""

from __future__ import annotations

import argparse
import json
import platform
import time
from pathlib import Path

import cv2
import mediapipe as mp

from src.config import Settings
from src.contract import FrameMeta
from src.detector import HandDetector
from src.metrics import VisionMetrics, percentile
from src.pipeline import FramePipeline
from tools import assets, frames


def load_frames(video: str | None, count: int) -> list[bytes]:
    if not video:
        return frames.sliding_sequence(assets.ensure_test_image(), count=min(count, 30))
    cap = cv2.VideoCapture(video)
    out = []
    while len(out) < count:
        ok, bgr = cap.read()
        if not ok:
            break
        out.append(frames.encode_jpeg(cv2.resize(bgr, (frames.FRAME_W, frames.FRAME_H))))
    cap.release()
    if not out:
        raise SystemExit(f"no frames read from {video}")
    return out


def environment() -> dict:
    return {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "processor": platform.processor(),
        "mediapipe": mp.__version__,
        "opencv": cv2.__version__,
    }


def stats(values: list[float]) -> dict:
    s = sorted(values)
    return {
        "n": len(s),
        "avg": round(sum(s) / len(s), 3),
        "p50": round(percentile(s, 50), 3),
        "p95": round(percentile(s, 95), 3),
        "max": round(s[-1], 3),
    }


def run_offline(args) -> dict:
    jpegs = load_frames(args.video, args.frames)
    detector = HandDetector(Settings(model_path=str(assets.ensure_model())))
    pipeline = FramePipeline(detector, VisionMetrics(), publisher=None)
    decode, infer, ser, detected = [], [], [], 0
    for i in range(args.warmup):
        pipeline.process(FrameMeta("bench-warmup", i, i * 33), jpegs[i % len(jpegs)])
    t0 = time.perf_counter()
    for i in range(args.frames):
        r = pipeline.process(FrameMeta("bench", i, i * 33), jpegs[i % len(jpegs)])
        decode.append(r.decode_ms)
        infer.append(r.inference_ms)
        ser.append(r.serialization_ms)
        detected += bool(r.message["hands"])
    elapsed = time.perf_counter() - t0
    detector.close()
    return {
        "mode": "offline",
        "frames": args.frames,
        "warmup_frames": args.warmup,
        "distinct_input_frames": len(jpegs),
        "detected_frames": detected,
        "elapsed_s": round(elapsed, 3),
        "processing_fps": round(args.frames / elapsed, 2),
        "decode_ms": stats(decode),
        "inference_ms": stats(infer),
        "serialization_ms": stats(ser),
    }


def _http(method: str, url: str, body: bytes | None = None, headers: dict | None = None):
    # stdlib only, so it also runs inside the service image (no httpx there)
    import urllib.request

    req = urllib.request.Request(url, data=body, headers=headers or {}, method=method)
    with urllib.request.urlopen(req, timeout=5) as resp:  # raises on HTTP errors
        data = resp.read()
    return json.loads(data) if data else None


def run_http(args) -> dict:
    jpegs = load_frames(args.video, 30)
    total = int(args.fps * args.seconds)
    base = args.url.rstrip("/")
    _http("POST", f"{base}/metrics/reset")
    start = time.perf_counter()
    for i in range(total):
        delay = start + i / args.fps - time.perf_counter()
        if delay > 0:
            time.sleep(delay)
        _http("POST", f"{base}/v1/frames", jpegs[i % len(jpegs)],
              {"Content-Type": "image/jpeg", "X-Session-Id": "bench-http",
               "X-Frame-Id": str(i), "X-Ts": str(int(time.time() * 1000))})
    elapsed = time.perf_counter() - start  # input window only
    time.sleep(1.0)
    snap = _http("GET", f"{base}/metrics")
    health = _http("GET", f"{base}/healthz")
    return {
        "mode": "http",
        "url": args.url,
        "target_input_fps": args.fps,
        "elapsed_s": round(elapsed, 3),
        "frames_submitted": total,
        "avg_input_fps": round(snap["input_frame_count"] / elapsed, 2),
        "avg_processing_fps": round(snap["processed_frame_count"] / elapsed, 2),
        "health": health,
        "metrics": snap,
    }


def run_mqtt(args) -> dict:
    """Act as the gateway: publish gateway-format packets to the broker, then read vision's /metrics."""
    import uuid

    import paho.mqtt.client as mqtt

    jpegs = load_frames(args.video, 30)
    total = int(args.fps * args.seconds)
    base = args.url.rstrip("/")
    session = uuid.uuid4().hex
    topic = f"{args.prefix.strip('/')}/frames/{session}"
    received: list[dict] = []
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="vision-bench-" + session[:8])
    client.on_message = lambda c, u, m: received.append(json.loads(m.payload))
    client.connect(args.broker_host, args.broker_port)
    client.subscribe(f"{args.prefix.strip('/')}/landmarks/{session}", qos=0)
    client.loop_start()
    time.sleep(0.3)  # let the SUBSCRIBE settle
    try:
        _http("POST", f"{base}/metrics/reset")
        start = time.perf_counter()
        for i in range(total):
            delay = start + i / args.fps - time.perf_counter()
            if delay > 0:
                time.sleep(delay)
            info = client.publish(topic, frames.gateway_packet(session, i, jpegs[i % len(jpegs)]), qos=0)
            if info.rc != mqtt.MQTT_ERR_SUCCESS:
                raise RuntimeError(f"MQTT publish failed: {info.rc}")
        elapsed = time.perf_counter() - start  # input window only
        time.sleep(1.0)
        snap = _http("GET", f"{base}/metrics")
        health = _http("GET", f"{base}/healthz")
    finally:
        client.loop_stop()
        client.disconnect()
    return {
        "mode": "mqtt",
        "broker": f"{args.broker_host}:{args.broker_port}",
        "topic": topic,
        "target_input_fps": args.fps,
        "elapsed_s": round(elapsed, 3),
        "frames_published": total,
        "landmarks_received": len(received),
        "avg_input_fps": round(snap["input_frame_count"] / elapsed, 2),
        "avg_processing_fps": round(snap["processed_frame_count"] / elapsed, 2),
        "health": health,
        "metrics": snap,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="mode", required=True)
    p_off = sub.add_parser("offline")
    p_off.add_argument("--frames", type=int, default=300)
    p_off.add_argument("--warmup", type=int, default=10)
    p_http = sub.add_parser("http")
    p_http.add_argument("--url", default="http://localhost:8001")
    p_http.add_argument("--fps", type=float, default=30)
    p_http.add_argument("--seconds", type=float, default=10)
    p_mqtt = sub.add_parser("mqtt")
    p_mqtt.add_argument("--url", default="http://localhost:8001", help="vision service (for /metrics)")
    p_mqtt.add_argument("--broker-host", default="localhost")
    p_mqtt.add_argument("--broker-port", type=int, default=11883)
    p_mqtt.add_argument("--prefix", default="hgw/v1")
    p_mqtt.add_argument("--fps", type=float, default=30)
    p_mqtt.add_argument("--seconds", type=float, default=10)
    for p in (p_off, p_http, p_mqtt):
        p.add_argument("--video", help="use frames from this video file instead of the test image")
        p.add_argument("--out", help="also write the JSON report to this file")
    args = parser.parse_args()

    if args.mode == "offline":
        report = run_offline(args)
    elif args.mode == "mqtt":
        report = run_mqtt(args)
    else:
        report = run_http(args)
    if args.mode not in ("http", "mqtt"):
        report["environment"] = environment()
    text = json.dumps(report, indent=2)
    print(text)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
