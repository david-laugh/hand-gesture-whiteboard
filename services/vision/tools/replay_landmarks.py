"""Replay recorded landmark messages to the broker, exactly as vision publishes them.

For the gesture developer: reproduce real camera input without a camera, gateway or vision.

    python -m tools.replay_landmarks samples/recorded/draw.jsonl                  # localhost:11883, original timing
    python -m tools.replay_landmarks samples/continuous_frames.json --loop --speed 0.5
    python -m tools.replay_landmarks rec.jsonl --broker hgw-mqtt:1883 --session-id my-test

Accepts JSON Lines (one message per line, recorder output) or a JSON array/object
(samples/*.json). Messages are validated against the contract before sending and are
published to {prefix}/landmarks/{session_id}, QoS 0. Timing follows the recorded `ts`
deltas (divided by --speed). `ts` values are sent as recorded unless --rebase-ts is given.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import paho.mqtt.client as mqtt

from src.contract import serialize, validate_landmarks_message


def load_messages(path: Path) -> list[dict]:
    text = path.read_text(encoding="utf-8").strip()
    if text.startswith("["):
        messages = json.loads(text)
    elif text.startswith("{") and "\n" not in text.strip():
        messages = [json.loads(text)]
    else:
        try:
            messages = [json.loads(line) for line in text.splitlines() if line.strip()]
        except json.JSONDecodeError:
            messages = [json.loads(text)]  # pretty-printed single object
    for m in messages:
        validate_landmarks_message(m)
    return messages


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("file", type=Path)
    parser.add_argument("--broker", default="localhost:11883", help="HOST:PORT (default: gateway's host port)")
    parser.add_argument("--prefix", default="hgw/v1")
    parser.add_argument("--session-id", help="override session_id (and topic) for every message")
    parser.add_argument("--speed", type=float, default=1.0, help="playback speed multiplier")
    parser.add_argument("--loop", action="store_true", help="repeat until Ctrl+C (frame_id keeps increasing)")
    parser.add_argument("--rebase-ts", action="store_true", help="rewrite ts to the current time, keeping deltas")
    args = parser.parse_args()

    messages = load_messages(args.file)
    if not messages:
        raise SystemExit(f"no messages in {args.file}")
    host, port = args.broker.rsplit(":", 1)
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.connect(host, int(port))  # fails loudly if the broker is not up
    client.loop_start()

    first_ts = messages[0]["ts"]
    span_ms = max(messages[-1]["ts"] - first_ts, 0) + 33
    frame_span = messages[-1]["frame_id"] - messages[0]["frame_id"] + 1
    loop_index = 0
    sent = 0
    print(f"replaying {len(messages)} message(s) from {args.file} to {args.broker} (speed x{args.speed})")
    try:
        while True:
            start = time.perf_counter()
            base_now = int(time.time() * 1000)
            for m in messages:
                delay = (m["ts"] - first_ts) / 1000 / args.speed - (time.perf_counter() - start)
                if delay > 0:
                    time.sleep(delay)
                out = dict(m)
                if args.session_id:
                    out["session_id"] = args.session_id
                # keep frame_id and ts increasing across loops
                out["frame_id"] = m["frame_id"] + loop_index * frame_span
                if args.rebase_ts:
                    out["ts"] = base_now + (m["ts"] - first_ts)
                else:
                    out["ts"] = m["ts"] + loop_index * span_ms
                client.publish(f"{args.prefix.strip('/')}/landmarks/{out['session_id']}", serialize(out), qos=0).wait_for_publish()
                sent += 1
            if not args.loop:
                break
            loop_index += 1
    except KeyboardInterrupt:
        pass
    finally:
        client.loop_stop()
        client.disconnect()
    print(f"sent {sent} message(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
