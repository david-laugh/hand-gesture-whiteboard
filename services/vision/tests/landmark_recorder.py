"""Stand-in for the gesture service (Container C) — TEST/E2E ONLY, not used by the service.

Subscribes to {prefix}/landmarks/+ exactly like C will, validates every message against
the agreed contract (and that the topic suffix equals session_id), keeps them in memory
(tests) and/or records them to disk.

    python -m tests.landmark_recorder --host localhost --port 11883
    python -m tests.landmark_recorder --host hgw-mqtt --port 1883 --save-dir recordings --print-every 30

--save-dir writes one JSON Lines file per session_id (= one camera take through the
gateway): recordings/<YYYYmmdd-HHMMSS>_<session_id[:8]>.jsonl
"""

from __future__ import annotations

import argparse
import json
import threading
import time
import uuid
from pathlib import Path

import paho.mqtt.client as mqtt

from src.contract import validate_landmarks_message


class _SessionStats:
    def __init__(self, path: Path | None) -> None:
        self.path = path
        self.count = 0
        self.detected = 0
        self.gaps = 0
        self.last_frame_id: int | None = None
        self.first_ts: int | None = None
        self.last_ts: int | None = None

    def add(self, message: dict) -> None:
        self.count += 1
        self.detected += bool(message["hands"])
        fid = message["frame_id"]
        if self.last_frame_id is not None and fid > self.last_frame_id + 1:
            self.gaps += fid - self.last_frame_id - 1
        self.last_frame_id = fid
        self.first_ts = message["ts"] if self.first_ts is None else self.first_ts
        self.last_ts = message["ts"]

    def summary(self) -> str:
        span_s = max((self.last_ts or 0) - (self.first_ts or 0), 1) / 1000
        fps = (self.count - 1) / span_s if self.count > 1 else 0.0
        where = f" → {self.path}" if self.path else ""
        return (f"{self.count} msgs, hand {self.detected / max(self.count, 1):.0%}, "
                f"frame_id gaps {self.gaps}, ~{fps:.1f} fps{where}")


class LandmarkRecorder:
    def __init__(
        self,
        host: str,
        port: int,
        *,
        prefix: str = "hgw/v1",
        verbose: bool = False,
        save_dir: str | None = None,
        print_every: int = 1,
        keep_messages: bool = True,
    ) -> None:
        self.host = host
        self.port = port
        self.prefix = prefix.strip("/")
        self.topic = f"{self.prefix}/landmarks/+"
        self.verbose = verbose
        self.save_dir = Path(save_dir) if save_dir else None
        self.print_every = max(1, print_every)
        self.keep_messages = keep_messages
        self.messages: list[dict] = []
        self.errors: list[str] = []
        self.sessions: dict[str, _SessionStats] = {}
        self._lock = threading.Lock()
        self._subscribed = threading.Event()
        self._client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="landmark-recorder-" + uuid.uuid4().hex[:8])
        self._client.reconnect_delay_set(1, 10)
        self._client.on_connect = self._on_connect
        self._client.on_subscribe = lambda *a: self._subscribed.set()
        self._client.on_message = self._on_message

    # -- lifecycle -------------------------------------------------------------------------
    def start(self, timeout: float = 10.0) -> "LandmarkRecorder":
        self._client.connect_async(self.host, self.port, 30)
        self._client.loop_start()
        if not self._subscribed.wait(timeout):
            self.stop()
            raise RuntimeError(f"recorder could not subscribe to {self.topic} on {self.host}:{self.port}")
        return self

    def stop(self) -> None:
        self._client.disconnect()
        self._client.loop_stop()

    def wait_for(self, count: int, timeout: float = 5.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self._lock:
                if len(self.messages) >= count:
                    return True
            time.sleep(0.02)
        return False

    # -- paho callbacks --------------------------------------------------------------------
    def _on_connect(self, client, userdata, flags, reason, properties) -> None:
        if not reason.is_failure:
            client.subscribe(self.topic, qos=0)
            if self.verbose:
                print(f"[recorder] connected to {self.host}:{self.port}, subscribed {self.topic}", flush=True)

    def _session(self, session_id: str) -> _SessionStats:
        stats = self.sessions.get(session_id)
        if stats is None:
            path = None
            if self.save_dir is not None:
                self.save_dir.mkdir(parents=True, exist_ok=True)
                path = self.save_dir / f"{time.strftime('%Y%m%d-%H%M%S')}_{session_id[:8]}.jsonl"
                print(f"[recorder] new session {session_id} → recording {path}", flush=True)
            stats = self.sessions[session_id] = _SessionStats(path)
        return stats

    def _on_message(self, client, userdata, msg: mqtt.MQTTMessage) -> None:
        text = msg.payload.decode("utf-8", errors="replace")
        try:
            message = json.loads(text)
            validate_landmarks_message(message)
            topic_session = msg.topic.rsplit("/", 1)[-1]
            if message["session_id"] != topic_session:
                raise ValueError(f"topic suffix {topic_session!r} != session_id {message['session_id']!r}")
        except Exception as exc:  # record and keep going
            with self._lock:
                self.errors.append(f"{type(exc).__name__}: {exc}")
            print(f"[recorder] CONTRACT ERROR on {msg.topic}: {type(exc).__name__}: {exc}", flush=True)
            return
        with self._lock:
            if self.keep_messages:
                self.messages.append(message)
            stats = self._session(message["session_id"])
            stats.add(message)
        if stats.path:
            with open(stats.path, "a", encoding="utf-8") as fh:
                fh.write(text + "\n")
        if self.verbose and stats.count % self.print_every == 0:
            hands = message["hands"]
            desc = f"{hands[0]['handedness']} {hands[0]['score']:.2f}" if hands else "no hand"
            print(f"[recorder] {message['session_id']} frame {message['frame_id']} ts {message['ts']}: {desc}"
                  f" | {stats.summary()}", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--port", type=int, default=11883)
    parser.add_argument("--prefix", default="hgw/v1")
    parser.add_argument("--save-dir", help="record one JSON Lines file per session_id into this directory")
    parser.add_argument("--print-every", type=int, default=1, help="print one line every N messages per session")
    args = parser.parse_args()
    recorder = LandmarkRecorder(args.host, args.port, prefix=args.prefix, verbose=True, save_dir=args.save_dir,
                                print_every=args.print_every, keep_messages=False)
    recorder._client.connect_async(args.host, args.port, 30)
    print(f"[recorder] waiting for broker {args.host}:{args.port} (topic {recorder.topic})", flush=True)
    try:
        recorder._client.loop_forever(retry_first_connection=True)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
