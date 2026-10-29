"""Inspect and curate landmark recordings made by the E2E gesture-recorder.

    python -m tools.recordings                       # list recordings/ with stats
    python -m tools.recordings show <file>           # per-file details
    python -m tools.recordings promote <file> draw   # validate + copy to samples/recorded/draw.jsonl

Recordings are the exact messages the gesture service (C) would receive through the
operational path (camera → gateway → MQTT → vision), one JSON object per line.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
from collections import Counter
from pathlib import Path

from src.contract import validate_landmarks_message
from tools.assets import VISION_DIR

RECORDINGS_DIR = VISION_DIR / "recordings"
CURATED_DIR = VISION_DIR / "samples" / "recorded"


def analyze(path: Path) -> dict:
    messages, errors = [], 0
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            message = json.loads(line)
            validate_landmarks_message(message)
            messages.append(message)
        except ValueError:
            errors += 1
    if not messages:
        return {"file": path.name, "messages": 0, "contract_errors": errors}
    frame_ids = [m["frame_id"] for m in messages]
    gaps = sum(max(0, b - a - 1) for a, b in zip(frame_ids, frame_ids[1:]))
    duration_s = (messages[-1]["ts"] - messages[0]["ts"]) / 1000
    handedness = Counter(m["hands"][0]["handedness"] for m in messages if m["hands"])
    detected = sum(1 for m in messages if m["hands"])
    return {
        "file": path.name,
        "session_id": messages[0]["session_id"],
        "messages": len(messages),
        "contract_errors": errors,
        "duration_s": round(duration_s, 2),
        "fps": round((len(messages) - 1) / duration_s, 1) if duration_s > 0 else None,
        "frame_id_range": [frame_ids[0], frame_ids[-1]],
        "frame_id_gaps": gaps,
        "hand_ratio": round(detected / len(messages), 3),
        "handedness": dict(handedness),
    }


def cmd_list(_args) -> int:
    files = sorted(RECORDINGS_DIR.glob("*.jsonl"))
    if not files:
        print(f"no recordings in {RECORDINGS_DIR}")
        return 0
    print(f"{'file':<34} {'msgs':>6} {'sec':>7} {'fps':>6} {'hand%':>6} {'gaps':>5} {'err':>4}  handedness")
    for path in files:
        s = analyze(path)
        if not s["messages"]:
            print(f"{s['file']:<34} {0:>6} {'-':>7} {'-':>6} {'-':>6} {'-':>5} {s['contract_errors']:>4}")
            continue
        print(f"{s['file']:<34} {s['messages']:>6} {s['duration_s']:>7} {s['fps'] or '-':>6} "
              f"{s['hand_ratio'] * 100:>5.0f}% {s['frame_id_gaps']:>5} {s['contract_errors']:>4}  {s['handedness']}")
    return 0


def _resolve(name: str) -> Path:
    path = Path(name)
    return path if path.is_file() else RECORDINGS_DIR / name


def cmd_show(args) -> int:
    print(json.dumps(analyze(_resolve(args.file)), indent=2, ensure_ascii=False))
    return 0


def cmd_promote(args) -> int:
    src = _resolve(args.file)
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", args.name):
        raise SystemExit("name must be lowercase letters, digits, '-' or '_' (e.g. draw, erase_back_of_hand)")
    stats = analyze(src)
    if not stats["messages"] or stats["contract_errors"]:
        raise SystemExit(f"refusing to promote {src.name}: {stats}")
    CURATED_DIR.mkdir(parents=True, exist_ok=True)
    dest = CURATED_DIR / f"{args.name}.jsonl"
    if dest.exists() and not args.force:
        raise SystemExit(f"{dest} exists (use --force to overwrite)")
    shutil.copyfile(src, dest)
    print(f"copied {src.name} → {dest}")
    print(json.dumps(stats, ensure_ascii=False))
    print("add a line for it to samples/recorded/README.md (gesture performed, camera, palm/back of hand)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd")
    sub.add_parser("list")
    p_show = sub.add_parser("show")
    p_show.add_argument("file")
    p_promote = sub.add_parser("promote")
    p_promote.add_argument("file")
    p_promote.add_argument("name")
    p_promote.add_argument("--force", action="store_true")
    args = parser.parse_args()
    return {"show": cmd_show, "promote": cmd_promote}.get(args.cmd, cmd_list)(args)


if __name__ == "__main__":
    raise SystemExit(main())
