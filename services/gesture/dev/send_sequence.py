"""가짜 vision(B): 손 없음 → 검지로 원 그리기 → 손가락 접기 → 손 없음 시퀀스를 C로 보낸다.

    # MQTT로 발행 (기본: localhost:11883, hgw/v1/landmarks/{session_id})
    python dev/send_sequence.py dev-01
    # WebSocket /ws/landmarks 로 보내기 (vision과 같은 방식, 결과는 C가 MQTT로 발행)
    python dev/send_sequence.py dev-01 --ws
    # HTTP /v1/classify 로 보내고 응답 출력
    python dev/send_sequence.py dev-01 --http --url http://localhost:8002
    # 레포 LandmarkMessage 형식으로 보내기 (기본은 vision 자체 형식)
    python dev/send_sequence.py dev-01 --format repo

services/gesture 폴더에서 실행한다.
"""
import argparse
import copy
import json
import math
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

FIXTURES = Path(__file__).resolve().parents[1] / "tests" / "fixtures"
INDEX_TIP = 8


def load(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


def build_sequence(fps: int) -> list[tuple[str, dict]]:
    """(라벨, 레포 형식 메시지 틀) 목록. frame_id/ts/session_id는 보낼 때 채운다."""
    no_hand, draw, fist = load("no_hand"), load("index_pose"), load("fist")
    tip = draw["hands"][0]["landmarks"][INDEX_TIP]
    seq = [("no_hand", no_hand)] * (fps // 2)

    # 검지 끝이 중심 (0.5, 0.45), 반지름 0.15인 원을 2초 동안 한 바퀴 그리도록 손 전체를 옮긴다
    steps = fps * 2
    for i in range(steps + 1):
        a = 2 * math.pi * i / steps
        dx = 0.5 + 0.15 * math.cos(a) - tip["x"]
        dy = 0.45 + 0.15 * math.sin(a) - tip["y"]
        msg = copy.deepcopy(draw)
        for p in msg["hands"][0]["landmarks"]:
            p["x"] += dx
            p["y"] += dy
        seq.append(("circle", msg))
        last = (dx, dy)

    # 원이 끝난 자리에서 주먹 쥐기
    folded = copy.deepcopy(fist)
    for p in folded["hands"][0]["landmarks"]:
        p["x"] += last[0]
        p["y"] += last[1]
    seq += [("fold", folded)] * (fps // 2)
    seq += [("no_hand", no_hand)] * (fps // 2)
    return seq


def to_vision_format(m: dict) -> dict:
    return {
        "type": "landmarks",
        "session_id": m["session_id"],
        "frame_id": m["frame_id"],
        "ts": m["timestamp_ms"],
        "hands": [
            {"handedness": h["handedness"], "score": h["confidence"],
             "landmarks": [[p["x"], p["y"], p["z"]] for p in h["landmarks"]]}
            for h in m["hands"]
        ],
        "gesture": None,
    }


def frames(args):
    for frame_id, (label, template) in enumerate(build_sequence(args.fps)):
        m = copy.deepcopy(template)
        m["session_id"] = args.session_id
        m["frame_id"] = frame_id
        m["trace_id"] = uuid.uuid4().hex
        m["timestamp_ms"] = int(time.time() * 1000)
        yield label, (to_vision_format(m) if args.format == "vision" else m)


def paced(args):
    """fps에 맞춰 일정한 간격으로 내보낸다."""
    interval = 1.0 / args.fps
    next_at = time.perf_counter()
    for item in frames(args):
        yield item
        next_at += interval
        time.sleep(max(0.0, next_at - time.perf_counter()))


def send_http(args) -> None:
    url = args.url.rstrip("/") + "/v1/classify"
    for label, msg in paced(args):
        req = urllib.request.Request(url, data=json.dumps(msg).encode(), method="POST",
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=2) as res:
                g = json.loads(res.read())
        except urllib.error.HTTPError as exc:
            print(f"{msg['frame_id']:4d} {label:8s} HTTP {exc.code} {exc.read().decode()[:200]}")
            continue
        print(f"{g['frame_id']:4d} {label:8s} {g['gesture']:10s} pointer={g['pointer']} "
              f"stroke_id={g['params'].get('stroke_id')}")


def send_mqtt(args) -> None:
    import paho.mqtt.client as mqtt

    connected = threading.Event()
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="send-sequence-" + uuid.uuid4().hex)
    client.on_connect = lambda c, u, f, reason, p: connected.set() if not reason.is_failure else None
    client.connect_async(args.host, args.port, 30)
    client.loop_start()
    if not connected.wait(5):
        client.loop_stop()
        sys.exit(f"MQTT 브로커 {args.host}:{args.port}에 연결하지 못했습니다")

    topic = f"{args.prefix.strip('/')}/landmarks/{args.session_id}"
    print(f"publish → {topic} ({args.format} format, {args.fps} fps)")
    count = 0
    for label, msg in paced(args):
        client.publish(topic, json.dumps(msg, separators=(",", ":")), qos=0, retain=False)
        count += 1
        print(f"{msg['frame_id']:4d} {label}")
    client.disconnect()
    client.loop_stop()
    print(f"{count} frames sent")


def send_ws(args) -> None:
    from websockets.sync.client import connect  # uvicorn[standard]에 포함

    print(f"send → {args.ws_url} ({args.format} format, {args.fps} fps)")
    count = 0
    with connect(args.ws_url, open_timeout=3) as ws:
        for label, msg in paced(args):
            ws.send(json.dumps(msg, separators=(",", ":")))  # 텍스트 메시지 1개 = 프레임 1개
            count += 1
            print(f"{msg['frame_id']:4d} {label}")
    print(f"{count} frames sent")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("session_id", nargs="?", default="dev-01")
    p.add_argument("--fps", type=int, default=15)
    p.add_argument("--format", choices=["vision", "repo"], default="vision")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--mqtt", action="store_true", help="MQTT로 발행 (기본)")
    mode.add_argument("--ws", action="store_true", help="WebSocket /ws/landmarks 로 전송")
    mode.add_argument("--http", action="store_true", help="HTTP /v1/classify 로 전송")
    p.add_argument("--host", default="localhost")
    p.add_argument("--port", type=int, default=11883)
    p.add_argument("--prefix", default="hgw/v1")
    p.add_argument("--url", default="http://localhost:8002")
    p.add_argument("--ws-url", default="ws://localhost:8002/ws/landmarks")
    args = p.parse_args()
    if args.http:
        send_http(args)
    elif args.ws:
        send_ws(args)
    else:
        send_mqtt(args)


if __name__ == "__main__":
    main()
