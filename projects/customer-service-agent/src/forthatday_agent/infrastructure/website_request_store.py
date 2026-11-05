"""Private, transactional intake; never creates a confirmed appointment."""
from contextlib import closing
import hashlib
import hmac
import json
from pathlib import Path
import secrets
import sqlite3
import time
from uuid import uuid4


class IntakeError(ValueError):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


class WebsiteRequestStore:
    def __init__(self, database_path: str | Path):
        self.database_path = str(database_path)
        with closing(self._connect()) as db, db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS website_requests (
                    id TEXT PRIMARY KEY, idempotency_key TEXT NOT NULL UNIQUE,
                    payload_hash TEXT NOT NULL, customer_name TEXT NOT NULL,
                    customer_phone TEXT NOT NULL, preferred_date TEXT NOT NULL,
                    preferred_time TEXT NOT NULL, message TEXT NOT NULL,
                    consent_version TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'new',
                    version INTEGER NOT NULL DEFAULT 1, created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS website_requests_created ON website_requests(created_at);
                CREATE INDEX IF NOT EXISTS website_requests_phone ON website_requests(customer_phone, created_at);
                CREATE TABLE IF NOT EXISTS website_request_events (
                    request_id TEXT NOT NULL, staff_id TEXT NOT NULL,
                    status TEXT NOT NULL, created_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS website_rate_events (ip_hash TEXT NOT NULL, created_at INTEGER NOT NULL);
                CREATE INDEX IF NOT EXISTS website_rate_events_ip ON website_rate_events(ip_hash, created_at);
                CREATE TABLE IF NOT EXISTS website_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            """)
            db.execute("INSERT OR IGNORE INTO website_settings VALUES ('rate_secret', ?)", (secrets.token_hex(32),))

    def _connect(self):
        db = sqlite3.connect(self.database_path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=10000")
        return db

    def submit(self, payload: dict[str, str], key: str, client_ip: str, *, now: int | None = None):
        now = int(time.time()) if now is None else now
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        with closing(self._connect()) as db, db:
            # Serialize deduplication and rate checks with the insert, including across processes.
            db.execute("BEGIN IMMEDIATE")
            previous = db.execute("SELECT id, payload_hash FROM website_requests WHERE idempotency_key=?", (key,)).fetchone()
            if previous:
                if not hmac.compare_digest(previous['payload_hash'], digest):
                    raise IntakeError(409, "本次提交内容已改变，请重新提交。")
                return previous['id'], False
            secret = db.execute("SELECT value FROM website_settings WHERE key='rate_secret'").fetchone()[0]
            ip_hash = hmac.new(secret.encode(), client_ip.encode(), hashlib.sha256).hexdigest()
            db.execute("DELETE FROM website_rate_events WHERE created_at < ?", (now - 86400,))
            count = db.execute("SELECT count(*) FROM website_rate_events WHERE ip_hash=? AND created_at>=?", (ip_hash, now-3600)).fetchone()[0]
            phone_count = db.execute("SELECT count(*) FROM website_requests WHERE customer_phone=? AND created_at>=?", (payload['customer_phone'], now-86400)).fetchone()[0]
            if count >= 10 or phone_count >= 3:
                raise IntakeError(429, "提交较频繁，请稍后再试；已提交的申请无需重复填写。")
            reference = 'FTD-' + uuid4().hex[:16].upper()
            db.execute("""INSERT INTO website_requests
                (id,idempotency_key,payload_hash,customer_name,customer_phone,preferred_date,
                 preferred_time,message,consent_version,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)""", (reference,key,digest,payload['customer_name'],payload['customer_phone'],payload['preferred_date'],payload['preferred_time'],payload['message'],'website-20261023',now,now))
            db.execute("INSERT INTO website_rate_events VALUES (?,?)", (ip_hash, now))
            return reference, True

    def list_requests(self, status: str, page: int = 0):
        where = " WHERE status=?" if status else ""
        args = (status,) if status else ()
        with closing(self._connect()) as db:
            total = db.execute("SELECT count(*) FROM website_requests" + where, args).fetchone()[0]
            rows = db.execute("SELECT * FROM website_requests" + where + " ORDER BY created_at DESC, id DESC LIMIT 25 OFFSET ?", (*args, page*25)).fetchall()
        return rows, total

    def update_status(self, request_id: str, status: str, version: int, staff_id: str):
        if status not in {'new', 'contacted', 'closed'}:
            raise ValueError("申请状态无效")
        with closing(self._connect()) as db, db:
            result = db.execute("UPDATE website_requests SET status=?,version=version+1,updated_at=? WHERE id=? AND version=?", (status,int(time.time()),request_id,version))
            if result.rowcount != 1:
                raise ValueError("申请已被其他店员更新，请刷新后重试")
            db.execute("INSERT INTO website_request_events VALUES (?,?,?,?)", (request_id,staff_id,status,int(time.time())))
