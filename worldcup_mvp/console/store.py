"""SQLite persistence for the local workspace; source identities and snapshots are immutable."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
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
        self._connections = threading.local()
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
                CREATE TABLE IF NOT EXISTS live_scores(match_id TEXT PRIMARY KEY REFERENCES matches(id), payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS live_score_snapshots(id INTEGER PRIMARY KEY, match_id TEXT NOT NULL REFERENCES matches(id), captured_at TEXT NOT NULL, digest TEXT NOT NULL, payload TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS live_snapshot_match ON live_score_snapshots(match_id,id);
                INSERT OR IGNORE INTO schema_versions VALUES(3);
                CREATE TABLE IF NOT EXISTS postmatch_reports(
                    id INTEGER PRIMARY KEY AUTOINCREMENT, match_id TEXT NOT NULL REFERENCES matches(id),
                    prediction_id TEXT NOT NULL REFERENCES analyses(id), digest TEXT NOT NULL,
                    created_at TEXT NOT NULL, payload TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS postmatch_prediction ON postmatch_reports(prediction_id,id);
                INSERT OR IGNORE INTO schema_versions VALUES(4);
                CREATE TABLE IF NOT EXISTS match_reports(
                    id INTEGER PRIMARY KEY AUTOINCREMENT, match_id TEXT NOT NULL REFERENCES matches(id),
                    prediction_id TEXT REFERENCES analyses(id), digest TEXT NOT NULL,
                    created_at TEXT NOT NULL, payload TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS match_report_subject ON match_reports(match_id,prediction_id,id);
                INSERT OR IGNORE INTO match_reports SELECT * FROM postmatch_reports;
                INSERT OR IGNORE INTO schema_versions VALUES(5);
                CREATE TABLE IF NOT EXISTS prediction_requests(
                    request_key TEXT PRIMARY KEY, match_id TEXT NOT NULL,
                    input_digest TEXT NOT NULL, analysis_id TEXT NOT NULL REFERENCES analyses(id));
                INSERT OR IGNORE INTO schema_versions VALUES(6);
                CREATE TABLE IF NOT EXISTS settlement_versions(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    analysis_id TEXT NOT NULL REFERENCES analyses(id),
                    digest TEXT NOT NULL, created_at TEXT NOT NULL, payload TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS settlement_analysis ON settlement_versions(analysis_id,id);
                INSERT OR IGNORE INTO schema_versions VALUES(7);
                CREATE TABLE IF NOT EXISTS daily_fixtures(
                    match_id TEXT PRIMARY KEY, day TEXT NOT NULL, payload TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS daily_fixture_day ON daily_fixtures(day);
                CREATE TABLE IF NOT EXISTS daily_forecasts(
                    id INTEGER PRIMARY KEY AUTOINCREMENT, match_id TEXT NOT NULL,
                    kind TEXT NOT NULL, captured_at TEXT NOT NULL, payload TEXT NOT NULL,
                    UNIQUE(match_id,kind));
                CREATE TABLE IF NOT EXISTS daily_outcomes(
                    id INTEGER PRIMARY KEY AUTOINCREMENT, forecast_id INTEGER NOT NULL,
                    digest TEXT NOT NULL, created_at TEXT NOT NULL, payload TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS daily_outcome_forecast ON daily_outcomes(forecast_id,id);
                INSERT OR IGNORE INTO schema_versions VALUES(8);
                CREATE TABLE IF NOT EXISTS daily_market_additions(
                    forecast_id INTEGER NOT NULL, code TEXT NOT NULL,
                    captured_at TEXT NOT NULL, payload TEXT NOT NULL,
                    PRIMARY KEY(forecast_id,code));
                INSERT OR IGNORE INTO schema_versions VALUES(9);
                CREATE TABLE IF NOT EXISTS daily_explorations(
                    forecast_id INTEGER NOT NULL, code TEXT NOT NULL,
                    captured_at TEXT NOT NULL, payload TEXT NOT NULL,
                    PRIMARY KEY(forecast_id,code));
                INSERT OR IGNORE INTO schema_versions VALUES(10);
                CREATE TABLE IF NOT EXISTS official_odds_cache(
                    match_id TEXT PRIMARY KEY REFERENCES matches(id), payload TEXT NOT NULL);
                INSERT OR IGNORE INTO schema_versions VALUES(11);
            """)

    def _open_connection(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        return db

    @contextmanager
    def connection_scope(self):
        """Reuse a handle within one operation, never across request/worker threads.

        This is not a transaction: each outer connect() still commits or rolls
        back separately, so no write lock is held during source HTTP requests.
        """
        if getattr(self._connections, 'db', None) is not None:
            yield
            return
        db = self._open_connection()
        self._connections.db = db
        self._connections.depth = 0
        try:
            yield
        finally:
            del self._connections.db
            del self._connections.depth
            db.close()

    @contextmanager
    def connect(self):
        shared = getattr(self._connections, 'db', None)
        if shared is not None:
            depth = self._connections.depth
            self._connections.depth += 1
            try:
                if depth:
                    yield shared
                else:
                    with shared:
                        yield shared
            finally:
                self._connections.depth -= 1
            return
        db = self._open_connection()
        try:
            with db:
                yield db
        finally:
            db.close()

    def live_scores(self):
        with self.connect() as db:
            return {
                r["match_id"]: json.loads(r["payload"])
                for r in db.execute("SELECT * FROM live_scores")
            }

    def save_postmatch_report(self, match_id, prediction_id, report):
        # Only timestamps vary on unchanged polls. A -> B -> A is a new correction.
        digest = fingerprint({k: v for k, v in report.items() if k != "observed_at"})
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            previous = db.execute(
                "SELECT * FROM match_reports WHERE match_id=? AND prediction_id IS ? ORDER BY id DESC LIMIT 1",
                (match_id, prediction_id),
            ).fetchone()
            if previous and previous["digest"] == digest:
                return {**json.loads(previous["payload"]), "id": previous["id"], "created_at": previous["created_at"]}
            created = now_iso()
            if previous:
                old = json.loads(previous["payload"])
                report = {**report, "previous_score": old.get("score"), "previous_status": old.get("status")}
            cursor = db.execute(
                "INSERT INTO match_reports(match_id,prediction_id,digest,created_at,payload) VALUES(?,?,?,?,?)",
                (match_id, prediction_id, digest, created, encode(report)),
            )
            return {**report, "id": cursor.lastrowid, "created_at": created}

    def review_updates(self, after=None):
        with self.connect() as db:
            if after is None:
                return {"cursor": db.execute("SELECT coalesce(max(id),0) FROM match_reports").fetchone()[0], "updates": []}
            rows = db.execute("SELECT * FROM match_reports WHERE id>? ORDER BY id LIMIT 50", (after,)).fetchall()
        return {"cursor": rows[-1]["id"] if rows else after, "updates": [
            {"id": r["id"], "match_id": r["match_id"], "created_at": r["created_at"],
             **{k: json.loads(r["payload"]).get(k) for k in ("fixture", "score", "status", "summary")}}
            for r in rows
        ]}

    def save_live_score(self, match_id, result=None, error=None):
        with self.connect() as db:
            previous = db.execute(
                "SELECT payload FROM live_scores WHERE match_id=?", (match_id,)
            ).fetchone()
            value = json.loads(previous[0]) if previous else {}
            checked = now_iso()
            if result is not None:
                value = {**result, "observed_at": checked}
                digest = fingerprint(result)
                last = db.execute(
                    "SELECT digest FROM live_score_snapshots WHERE match_id=? ORDER BY id DESC LIMIT 1",
                    (match_id,),
                ).fetchone()
                if not last or last[0] != digest:
                    db.execute(
                        "INSERT INTO live_score_snapshots(match_id,captured_at,digest,payload) VALUES(?,?,?,?)",
                        (match_id, checked, digest, encode(value)),
                    )
            value.update(checked_at=checked, error=error)
            db.execute(
                "INSERT OR REPLACE INTO live_scores VALUES(?,?)",
                (match_id, encode(value)),
            )

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

    def saved_request(self, request_key, match_id):
        if request_key is None:
            return None
        if not isinstance(request_key,str) or not 1 <= len(request_key) <= 150:
            raise ValueError('预测请求标识无效')
        with self.connect() as db:
            row = db.execute('SELECT r.match_id,a.payload FROM prediction_requests r JOIN analyses a ON a.id=r.analysis_id WHERE r.request_key=?', (request_key,)).fetchone()
        if row and row['match_id'] != match_id:
            raise ValueError('预测请求标识已用于其他比赛')
        return json.loads(row['payload']) if row else None

    def save_settlement(self, analysis_id, value):
        digest = fingerprint(value)
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            latest = db.execute('SELECT id,digest,payload FROM settlement_versions WHERE analysis_id=? ORDER BY id DESC LIMIT 1',(analysis_id,)).fetchone()
            if latest and latest['digest']==digest:
                return json.loads(latest['payload'])
            result = {**value,'analysis_id':analysis_id,'digest':digest,'created_at':now_iso(),
                      'previous_id':latest['id'] if latest else None}
            cursor = db.execute('INSERT INTO settlement_versions(analysis_id,digest,created_at,payload) VALUES(?,?,?,?)',
                                (analysis_id,digest,result['created_at'],encode(result)))
            result['id'] = cursor.lastrowid
            db.execute('UPDATE settlement_versions SET payload=? WHERE id=?',(encode(result),result['id']))
        return result

    def save_analysis(self, match_id, result, inputs, *, expected_fixture=None, request_key=None):
        digest = fingerprint({"data": inputs, "model": result["model_version"]})
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if expected_fixture is not None:
                from .readiness import fixture_key
                from .sources import parse_date
                from .quality import age_seconds
                current = db.execute('SELECT payload,updated_at FROM matches WHERE id=?',(match_id,)).fetchone()
                if not current or fixture_key(json.loads(current['payload'])) != expected_fixture:
                    raise ValueError('处理期间比赛身份或开球时间已变更')
                if parse_date(expected_fixture['kickoff']) <= datetime.now(timezone.utc) or not -30 <= age_seconds(current['updated_at']) <= 180:
                    raise ValueError('归档时比赛已开赛或状态核验过期')
                latest_detail = db.execute('SELECT payload FROM detail_cache WHERE match_id=?',(match_id,)).fetchone()
                latest_live = db.execute('SELECT payload FROM live_scores WHERE match_id=?',(match_id,)).fetchone()
                current_detail = json.loads(latest_detail[0]) if latest_detail else {}
                current_live = json.loads(latest_live[0]) if latest_live else {}
                blocked_states = ('live','completed','cancelled','postponed','suspended')
                if current_detail.get('cancelled') or current_detail.get('official_score') or current_detail.get('context',{}).get('state') in blocked_states or current_live.get('state') in blocked_states:
                    raise ValueError('归档时比赛状态已变更')
            if request_key is not None:
                old = db.execute('SELECT * FROM prediction_requests WHERE request_key=?',(request_key,)).fetchone()
                if old:
                    if old['match_id'] != match_id or old['input_digest'] != digest:
                        raise ValueError('相同预测请求标识对应不同输入')
                    return json.loads(db.execute('SELECT payload FROM analyses WHERE id=?',(old['analysis_id'],)).fetchone()[0])
            latest = db.execute(
                "SELECT version,input_digest,payload FROM analyses WHERE match_id=? ORDER BY version DESC LIMIT 1",
                (match_id,),
            ).fetchone()
            if (
                latest
                and latest["input_digest"] == digest
                and not result.get("narrative")
            ):
                if request_key is not None:
                    saved = json.loads(latest['payload'])
                    db.execute('INSERT INTO prediction_requests VALUES(?,?,?,?)',(request_key,match_id,digest,saved['id']))
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
            if request_key is not None:
                db.execute('INSERT INTO prediction_requests VALUES(?,?,?,?)',(request_key,match_id,digest,result['id']))
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
