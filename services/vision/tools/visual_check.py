"""Local visual check for the vision developer (host only, NOT part of the service).

Shows the camera (or a video/image file) with the 21 detected landmarks, the skeleton,
handedness/score and FPS. The skeleton is drawn here only for verification; the service
never sends images, the frontend draws the skeleton from landmark coordinates.

    python -m tools.visual_check --source 0                      # webcam 0
    python -m tools.visual_check --source clip.mp4 --dump out.jsonl
    python -m tools.visual_check --source 0 --mqtt localhost:11883   # also publish to hgw/v1/landmarks/{session}
    python -m tools.visual_check --source tests/data/woman_hands.jpg --save annotated.jpg --no-window

Frames go through the same path as the service: JPEG encode → decode → HandLandmarker
(VIDEO mode) → contract message. No horizontal flip is applied.
Keys: q / ESC to quit.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import cv2
from mediapipe.tasks.python import vision as mp_vision

from src.config import Settings
from src.contract import FrameMeta, validate_landmarks_message
from src.detector import HandDetector
from src.metrics import RateMeter, VisionMetrics
from src.pipeline import FramePipeline
from tools import assets, frames

CONNECTIONS = [(c.start, c.end) for c in mp_vision.HandLandmarksConnections.HAND_CONNECTIONS]
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp"}


def draw(bgr, message: dict, fps: float, inference_ms: float) -> None:
    h, w = bgr.shape[:2]
    for hand in message["hands"]:
        pts = [(int(x * w), int(y * h)) for x, y, _ in hand["landmarks"]]
        for a, b in CONNECTIONS:
            cv2.line(bgr, pts[a], pts[b], (0, 255, 0), 2)
        for i, p in enumerate(pts):
            cv2.circle(bgr, p, 4, (0, 0, 255) if i in (4, 8, 12, 16, 20) else (255, 255, 255), -1)
        wx, wy = pts[0]
        cv2.putText(bgr, f"{hand['handedness']} {hand['score']:.2f}", (wx + 8, wy + 20),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)
    status = f"frame {message['frame_id']}  hands {len(message['hands'])}  fps {fps:.1f}  infer {inference_ms:.1f} ms"
    cv2.putText(bgr, status, (10, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 0), 4)
    cv2.putText(bgr, status, (10, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1)


def open_source(source: str):
    if source.isdigit():
        cap = cv2.VideoCapture(int(source))
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, frames.FRAME_W)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, frames.FRAME_H)
        cap.set(cv2.CAP_PROP_FPS, 30)
        return cap
    if Path(source).suffix.lower() in IMAGE_EXTS:
        return None
    return cv2.VideoCapture(source)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", default="0", help="camera index, video file, or image file")
    parser.add_argument("--model", default=str(assets.MODEL_PATH))
    parser.add_argument("--session-id", default="local-camera")
    parser.add_argument("--mqtt", metavar="HOST:PORT", help="also publish each message to {prefix}/landmarks/{session-id}")
    parser.add_argument("--prefix", default="hgw/v1")
    parser.add_argument("--dump", help="append every contract message to this JSON Lines file")
    parser.add_argument("--save", help="save the last annotated frame to this image file")
    parser.add_argument("--max-frames", type=int, default=0, help="stop after N frames (0 = no limit)")
    parser.add_argument("--no-window", action="store_true")
    args = parser.parse_args()

    if not Path(args.model).is_file():
        assets.ensure_model()
    detector = HandDetector(Settings(model_path=args.model))
    pipeline = FramePipeline(detector, VisionMetrics(), publisher=None)

    mqttc = None
    if args.mqtt:
        import paho.mqtt.client as mqtt

        host, port = args.mqtt.rsplit(":", 1)
        mqttc = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        mqttc.connect(host, int(port))  # fails loudly if the broker is not up
        mqttc.loop_start()
        topic = f"{args.prefix.strip('/')}/landmarks/{args.session_id}"
        print("publishing to", topic, "on", args.mqtt)

    dump = open(args.dump, "a", encoding="utf-8") if args.dump else None
    still = None
    cap = open_source(args.source)
    if cap is None:
        still = frames.load_bgr(Path(args.source))
    elif not cap.isOpened():
        print(f"cannot open source {args.source!r}", file=sys.stderr)
        return 1

    rate = RateMeter(window_s=2.0)
    frame_id = 0
    annotated = None
    try:
        while True:
            if still is not None:
                bgr = still.copy()
            else:
                ok, bgr = cap.read()
                if not ok:
                    break
            meta = FrameMeta(args.session_id, frame_id, int(time.time() * 1000), bgr.shape[1], bgr.shape[0])
            result = pipeline.process(meta, frames.encode_jpeg(bgr))
            validate_landmarks_message(result.message)
            rate.mark()
            if mqttc is not None:
                mqttc.publish(topic, result.payload, qos=0)
            if dump is not None:
                dump.write(result.payload + "\n")
            annotated = bgr
            draw(annotated, result.message, rate.rate(), result.inference_ms)
            if not args.no_window:
                cv2.imshow("vision visual check (q to quit)", annotated)
                if cv2.waitKey(1) & 0xFF in (ord("q"), 27):
                    break
            frame_id += 1
            if (args.max_frames and frame_id >= args.max_frames) or (still is not None and args.no_window):
                break
    finally:
        if args.save and annotated is not None:
            frames.save_image(Path(args.save), annotated)
            print("saved", args.save)
        if cap is not None:
            cap.release()
        if dump is not None:
            dump.close()
        if mqttc is not None:
            mqttc.loop_stop()
            mqttc.disconnect()
        detector.close()
        if not args.no_window:
            cv2.destroyAllWindows()
    print(f"processed {frame_id} frame(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
