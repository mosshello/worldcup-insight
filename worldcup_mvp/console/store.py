"""SQLite persistence for the local workspace; source identities and snapshots are immutable."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def fingerprint(value):
    return hashlib.sha256(encode(value).encode()).hexdigest()


class Store:
    def __init__(self, path=None):
        self.path = Path(
            path
            or os.environ.get("FOOTBALL_DB_PATH")
            or ROOT / "data" / "console" / "football.sqlite3"
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS schema_versions(version INTEGER PRIMARY KEY);
                INSERT OR IGNORE INTO schema_versions VALUES(1);
                CREATE TABLE IF NOT EXISTS matches(
                    id TEXT PRIMARY KEY, provider TEXT NOT NULL, provider_id TEXT NOT NULL,
                    kickoff TEXT, sale_date TEXT, updated_at TEXT NOT NULL, payload TEXT NOT NULL,
                    UNIQUE(provider, provider_id));
                CREATE INDEX IF NOT EXISTS match_date_idx ON matches(sale_date, kickoff);
                CREATE TABLE IF NOT EXISTS snapshots(
                    id INTEGER PRIMARY KEY, match_id TEXT NOT NULL REFERENCES matches(id),
                    digest TEXT NOT NULL, captured_at TEXT NOT NULL, payload TEXT NOT NULL,
                    UNIQUE(match_id,digest));
                CREATE TABLE IF NOT EXISTS detail_cache(
                    match_id TEXT PRIMARY KEY REFERENCES matches(id), captured_at TEXT NOT NULL, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS analyses(
                    id TEXT PRIMARY KEY, match_id TEXT NOT NULL REFERENCES matches(id),
                    version INTEGER NOT NULL, created_at TEXT NOT NULL, model_version TEXT NOT NULL,
                    input_digest TEXT NOT NULL, input_snapshot TEXT NOT NULL, payload TEXT NOT NULL,
                    UNIQUE(match_id,version));
                CREATE TABLE IF NOT EXISTS workspace_state(
                    workspace_id TEXT NOT NULL, kind TEXT NOT NULL, item_key TEXT NOT NULL,
                    value TEXT NOT NULL, updated_at TEXT NOT NULL,
                    PRIMARY KEY(workspace_id,kind,item_key));
                CREATE TABLE IF NOT EXISTS source_status(
                    id TEXT PRIMARY KEY, checked_at TEXT NOT NULL, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS detail_snapshots(
                    id INTEGER PRIMARY KEY, match_id TEXT NOT NULL REFERENCES matches(id),
                    digest TEXT NOT NULL, captured_at TEXT NOT NULL, payload TEXT NOT NULL,
                    UNIQUE(match_id,digest));
                CREATE TABLE IF NOT EXISTS evidence(
                    id TEXT PRIMARY KEY, match_id TEXT NOT NULL REFERENCES matches(id),
                    created_at TEXT NOT NULL, payload TEXT NOT NULL);
                INSERT OR IGNORE INTO schema_versions VALUES(2);
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    def upsert_matches(self, matches, captured_at):
        with self.connect() as db:
            for m in matches:
                payload = encode(m)
                db.execute(
                    """INSERT INTO matches VALUES(?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
                    kickoff=excluded.kickoff,sale_date=excluded.sale_date,updated_at=excluded.updated_at,payload=excluded.payload""",
                    (
                        m["id"],
                        m["provider"],
                        m["provider_id"],
                        m["kickoff"],
                        m["sale_date"],
                        captured_at,
                        payload,
                    ),
                )
                db.execute(
                    "INSERT OR IGNORE INTO snapshots(match_id,digest,captured_at,payload) VALUES(?,?,?,?)",
                    (m["id"], fingerprint(m["markets"]), captured_at, payload),
                )

    def list_matches(self, date=None):
        with self.connect() as db:
            rows = db.execute(
                "SELECT payload,updated_at FROM matches"
                + (" WHERE sale_date=?" if date else "")
                + " ORDER BY kickoff",
                (date,) if date else (),
            ).fetchall()
        return [
            {**json.loads(r["payload"]), "captured_at": r["updated_at"]} for r in rows
        ]

    def match(self, match_id):
        with self.connect() as db:
            row = db.execute(
                "SELECT payload,updated_at FROM matches WHERE id=?", (match_id,)
            ).fetchone()
        return (
            {**json.loads(row["payload"]), "captured_at": row["updated_at"]}
            if row
            else None
        )

    def detail(self, match_id):
        with self.connect() as db:
            row = db.execute(
                "SELECT payload,captured_at FROM detail_cache WHERE match_id=?",
                (match_id,),
            ).fetchone()
        return (
            {"captured_at": row["captured_at"], "data": json.loads(row["payload"])}
            if row
            else None
        )

    def save_detail(self, match_id, data):
        with self.connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO detail_cache VALUES(?,?,?)",
                (match_id, now_iso(), encode(data)),
            )
            snapshot = {k: v for k, v in data.items() if k != "versions"}
            db.execute(
                "INSERT OR IGNORE INTO detail_snapshots(match_id,digest,captured_at,payload) VALUES(?,?,?,?)",
                (match_id, fingerprint(snapshot), now_iso(), encode(snapshot)),
            )

    def versions(self, match_id=None):
        with self.connect() as db:
            rows = db.execute(
                "SELECT payload FROM analyses"
                + (" WHERE match_id=?" if match_id else "")
                + " ORDER BY created_at DESC,version DESC",
                (match_id,) if match_id else (),
            ).fetchall()
        return [json.loads(r[0]) for r in rows]

    def save_analysis(self, match_id, result, inputs):
        digest = fingerprint({"data": inputs, "model": result["model_version"]})
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            latest = db.execute(
                "SELECT version,input_digest,payload FROM analyses WHERE match_id=? ORDER BY version DESC LIMIT 1",
                (match_id,),
            ).fetchone()
            if (
                latest
                and latest["input_digest"] == digest
                and not result.get("narrative")
            ):
                return json.loads(latest["payload"])
            result = {
                **result,
                "id": str(uuid.uuid4()),
                "matchId": match_id,
                "version": (latest["version"] if latest else 0) + 1,
                "created": now_iso(),
                "input_digest": digest,
            }
            db.execute(
                "INSERT INTO analyses VALUES(?,?,?,?,?,?,?,?)",
                (
                    result["id"],
                    match_id,
                    result["version"],
                    result["created"],
                    result["model_version"],
                    digest,
                    encode(inputs),
                    encode(result),
                ),
            )
        return result

    def set_state(self, kind, key, value, workspace="local"):
        if kind not in {"favorites", "notes", "reviewNotes", "plans", "drafts"}:
            raise ValueError("不支持的保存类型")
        if (
            not isinstance(key, str)
            or not key
            or len(key) > 150
            or len(encode(value)) > 16000
        ):
            raise ValueError("保存内容超出允许范围")
        with self.connect() as db:
            if value is None or (kind == "favorites" and value is False):
                db.execute(
                    "DELETE FROM workspace_state WHERE workspace_id=? AND kind=? AND item_key=?",
                    (workspace, kind, key),
                )
            else:
                db.execute(
                    "INSERT OR REPLACE INTO workspace_state VALUES(?,?,?,?,?)",
                    (workspace, kind, key, encode(value), now_iso()),
                )

    def add_evidence(self, match_id, value):
        from urllib.parse import urlsplit
        from .sources import parse_date

        if not self.match(match_id):
            raise ValueError("比赛不存在")
        kind = value.get("kind")
        text, url = value.get("text", ""), value.get("source_url", "")
        if (
            kind not in ("fact", "opinion")
            or not isinstance(text, str)
            or not 1 <= len(text.strip()) <= 1500
        ):
            raise ValueError("请选择事实或观点，并填写不超过1500字的内容")
        if (
            not isinstance(url, str)
            or len(url) > 1500
            or urlsplit(url).scheme not in ("http", "https")
            or not urlsplit(url).netloc
        ):
            raise ValueError("请填写有效来源链接")
        try:
            published = parse_date(value.get("published_at", ""))
            if published.tzinfo is None or published > datetime.now(timezone.utc):
                raise ValueError()
        except (ValueError, TypeError, AttributeError):
            raise ValueError("发布时间无效或晚于当前时间") from None
        precision = value.get("published_precision", "datetime")
        if precision not in ("date", "datetime"):
            raise ValueError("发布时间精度无效")
        result = {
            "id": str(uuid.uuid4()),
            "match_id": match_id,
            "kind": kind,
            "text": text.strip(),
            "source_url": url,
            "published_at": published.isoformat(),
            "published_precision": precision,
            "recorded_at": now_iso(),
            "review_status": "manual",
            "used_for_training": False,
        }
        with self.connect() as db:
            db.execute(
                "INSERT INTO evidence VALUES(?,?,?,?)",
                (result["id"], match_id, result["recorded_at"], encode(result)),
            )
        return result

    def evidence(self, match_id):
        with self.connect() as db:
            return [
                json.loads(r[0])
                for r in db.execute(
                    "SELECT payload FROM evidence WHERE match_id=? ORDER BY created_at",
                    (match_id,),
                )
            ]

    def state(self, workspace="local"):
        state = {
            "favorites": [],
            "notes": {},
            "reviewNotes": {},
            "plans": [],
            "drafts": {},
            "versions": {},
        }
        with self.connect() as db:
            rows = db.execute(
                "SELECT kind,item_key,value FROM workspace_state WHERE workspace_id=? ORDER BY updated_at DESC",
                (workspace,),
            ).fetchall()
        for row in rows:
            k, key, value = row["kind"], row["item_key"], json.loads(row["value"])
            if k == "favorites":
                state[k].append(key)
            elif k == "plans":
                state[k].append(value)
            else:
                state[k][key] = value
        for version in self.versions():
            state["versions"].setdefault(version["matchId"], []).append(version)
        return state

    def status(self, source, value):
        with self.connect() as db:
            prior = db.execute(
                "SELECT payload FROM source_status WHERE id=?", (source,)
            ).fetchone()
            previous = json.loads(prior[0]) if prior else {}
            if not value.get("last_success") and previous.get("last_success"):
                value = {**value, "last_success": previous["last_success"]}
            db.execute(
                "INSERT OR REPLACE INTO source_status VALUES(?,?,?)",
                (source, now_iso(), encode(value)),
            )

    def statuses(self):
        with self.connect() as db:
            return [
                {
                    "id": r["id"],
                    "checked_at": r["checked_at"],
                    **json.loads(r["payload"]),
                }
                for r in db.execute("SELECT * FROM source_status").fetchall()
            ]
