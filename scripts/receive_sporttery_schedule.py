"""Forced SSH command on the Linux receiver. Accept only a newer official snapshot."""
import fcntl
import json
import os
from pathlib import Path
import signal
import sys
from tempfile import NamedTemporaryFile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from worldcup_mvp.console.sources import parse_schedule_snapshot


def main():
    if os.environ.get("SSH_ORIGINAL_COMMAND") != "schedule":
        raise SystemExit("schedule uploads only")
    signal.alarm(30)
    dest = Path("/opt/football-console/runtime/schedule/latest.json")
    raw = sys.stdin.buffer.read(8_000_001)
    with (dest.parent / ".receive.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        previous = json.loads(dest.read_bytes())["captured_at"] if dest.exists() else None
        rows, captured, _ = parse_schedule_snapshot(raw, previous)
        if captured != previous:
            with NamedTemporaryFile(dir=dest.parent, delete=False) as temp:
                try:
                    temp.write(raw)
                    temp.flush()
                    os.fchmod(temp.fileno(), 0o644)
                    os.replace(temp.name, dest)
                finally:
                    Path(temp.name).unlink(missing_ok=True)
    print(captured, len(rows))


if __name__ == "__main__":
    main()
