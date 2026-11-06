"""Write one verified official match-calculator observation as UTF-8 JSON to stdout."""
import argparse
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from worldcup_mvp.console import sources


def capture():
    endpoint = "getMatchCalculatorV1.qry"
    data = sources.official(endpoint, channel="c", poolCode=",".join(sources.MARKETS))
    captured = sources.now_iso()
    matches = sources.normalize_schedule(data)
    if not matches or len({match["id"] for match in matches}) != len(matches):
        raise sources.SourceError("体彩赛程为空或包含重复场次")
    snapshot = {"source": sources.SPORTTERY + endpoint, "captured_at": captured, "value": data}
    return json.dumps(snapshot, ensure_ascii=False).encode("utf-8")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--upload", help="SSH destination restricted to receiving official schedules")
    args = parser.parse_args()
    if not args.upload:
        sys.stdout.buffer.write(capture())
        return
    directory = Path(os.environ["LOCALAPPDATA"]) / "FootballConsoleRelay"
    log = logging.getLogger("schedule")
    handler = RotatingFileHandler(directory / "sync.log", maxBytes=250_000, backupCount=2, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
    log.addHandler(handler)
    log.setLevel(logging.INFO)
    try:
        ssh = Path(os.environ["SystemRoot"]) / "System32/OpenSSH/ssh.exe"
        result = subprocess.run(
            [str(ssh), "-F", "NUL", "-T", "-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes",
             "-o", "StrictHostKeyChecking=yes", "-o", "UserKnownHostsFile=known_hosts",
             "-o", "ConnectTimeout=10", "-i", "upload_key", args.upload, "schedule"],
            input=capture(), cwd=directory, capture_output=True, timeout=35,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        if result.returncode:
            raise RuntimeError(result.stderr.decode(errors="replace")[:400])
        log.info(result.stdout.decode().strip())
    except Exception as exc:
        log.error("%s", exc)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
