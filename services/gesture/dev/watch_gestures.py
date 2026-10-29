"""가짜 canvas(D): {prefix}/gestures/+ 를 구독해 받은 GestureMessage를 한 줄씩 출력한다.

    python dev/watch_gestures.py                 # localhost:11883, hgw/v1/gestures/+
    python dev/watch_gestures.py --port 1883
"""
import argparse
import json
import uuid

import paho.mqtt.client as mqtt


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--host", default="localhost")
    p.add_argument("--port", type=int, default=11883)
    p.add_argument("--prefix", default="hgw/v1")
    args = p.parse_args()
    topic = f"{args.prefix.strip('/')}/gestures/+"

    def on_connect(client, userdata, flags, reason, properties):
        if reason.is_failure:
            print(f"connect failed: {reason}")
            return
        client.subscribe(topic, qos=0)
        print(f"subscribed {topic} @ {args.host}:{args.port}")

    def on_message(client, userdata, message):
        try:
            g = json.loads(message.payload)
            pointer = g["pointer"]
            pos = f"({pointer['x']:.3f}, {pointer['y']:.3f})" if pointer else "None"
            print(f"{g['session_id']} {g['frame_id']:4d} {g['gesture']:10s} pointer={pos:16s} "
                  f"stroke_id={g['params'].get('stroke_id')}")
        except Exception as exc:  # 가짜 D라서 깨진 메시지도 그냥 보여 준다
            print(f"{message.topic} unreadable: {exc}: {message.payload[:200]!r}")

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="watch-gestures-" + uuid.uuid4().hex)
    client.on_connect = on_connect
    client.on_message = on_message
    client.reconnect_delay_set(1, 10)
    client.connect_async(args.host, args.port, 30)
    try:
        client.loop_forever(retry_first_connection=True)
    except KeyboardInterrupt:
        client.disconnect()


if __name__ == "__main__":
    main()
