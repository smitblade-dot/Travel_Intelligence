"""Authoritative persistent queue and state: one SQLite database (WAL).

One database file is the single source of truth. It must live on one host's
persistent volume. Copies of the file on other machines are NOT a shared store.
All timestamps are UTC ISO-8601.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

STATES = ("pending", "running", "blocked", "failed", "completed", "cancelled")
ACTIVE_FOR_DEDUPE = ("pending", "running", "blocked", "completed")

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS tasks(
  id TEXT PRIMARY KEY, dedupe_key TEXT NOT NULL, business_application TEXT NOT NULL,
  substream TEXT NOT NULL, worker TEXT NOT NULL, task_type TEXT NOT NULL,
  priority INTEGER NOT NULL DEFAULT 50, state TEXT NOT NULL, depends_on TEXT NOT NULL DEFAULT '[]',
  inputs TEXT NOT NULL DEFAULT '{}', input_fingerprint TEXT NOT NULL, outputs TEXT,
  evidence TEXT NOT NULL DEFAULT '[]', attempts INTEGER NOT NULL DEFAULT 0,
  max_attempts INTEGER NOT NULL DEFAULT 3, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  started_at TEXT, finished_at TEXT, lease_owner TEXT, lease_expires TEXT,
  blocking_reason TEXT, requires_human INTEGER NOT NULL DEFAULT 0, checkpoint TEXT,
  mode TEXT NOT NULL DEFAULT 'none', paid INTEGER NOT NULL DEFAULT 0,
  cost_estimated_usd REAL NOT NULL DEFAULT 0, cost_confirmed_usd REAL NOT NULL DEFAULT 0,
  model_usage TEXT NOT NULL DEFAULT '[]', error TEXT);
CREATE UNIQUE INDEX IF NOT EXISTS ux_tasks_dedupe ON tasks(dedupe_key)
  WHERE state IN ('pending','running','blocked','completed');
CREATE INDEX IF NOT EXISTS ix_tasks_state ON tasks(state, priority);
CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT, ts TEXT NOT NULL,
  kind TEXT NOT NULL, detail TEXT);
CREATE TABLE IF NOT EXISTS reservations(id TEXT PRIMARY KEY, task_id TEXT NOT NULL, ts TEXT NOT NULL,
  day TEXT NOT NULL, month TEXT NOT NULL, amount_usd REAL NOT NULL, state TEXT NOT NULL,
  actual_usd REAL, model TEXT, worker TEXT);
CREATE TABLE IF NOT EXISTS usage(id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, day TEXT NOT NULL,
  month TEXT NOT NULL, task_id TEXT NOT NULL, worker TEXT, model TEXT, input_tokens INTEGER,
  output_tokens INTEGER, gross_cost_usd REAL NOT NULL, credits_usd REAL, payable_usd REAL,
  amount_status TEXT NOT NULL, currency TEXT NOT NULL DEFAULT 'USD', cost_source TEXT NOT NULL DEFAULT 'anthropic_api');
CREATE TABLE IF NOT EXISTS results_cache(fingerprint TEXT PRIMARY KEY, result TEXT NOT NULL, ts TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS locks(name TEXT PRIMARY KEY, owner TEXT NOT NULL, expires TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS control(key TEXT PRIMARY KEY, value TEXT NOT NULL, ts TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS fx_rates(date TEXT NOT NULL, base TEXT NOT NULL, quote TEXT NOT NULL,
  rate REAL NOT NULL, source TEXT NOT NULL, PRIMARY KEY(date, base, quote));
CREATE TABLE IF NOT EXISTS runs(id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, kind TEXT NOT NULL,
  ok INTEGER NOT NULL, detail TEXT);
"""


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def fingerprint(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()[:32]


def task_dedupe_key(task_type, subject, input_fingerprint, time_bucket=None, application="Travel Intelligence"):
    """The single deduplication key format for the queue (application, type, subject, inputs, window)."""
    return "|".join([application, task_type, subject, input_fingerprint, str(time_bucket or "")])


class Store:
    def __init__(self, path, clock=utc_now):
        self.path = str(path)
        self.clock = clock
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._init()

    def _connect(self):
        if self.path == ":memory:":
            raise RuntimeError("Use a file path; in-memory databases are not shared between connections")
        db = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA busy_timeout=30000")
        return db

    def _init(self):
        db = self._connect()
        try:
            db.executescript(SCHEMA)
            db.execute("INSERT OR IGNORE INTO meta VALUES('schema_version','1')")
        finally:
            db.close()

    @contextlib.contextmanager
    def tx(self):
        db = self._connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.execute("COMMIT")
        except BaseException:
            with contextlib.suppress(Exception):
                db.execute("ROLLBACK")
            raise
        finally:
            db.close()

    def now(self) -> str:
        return iso(self.clock())

    def event(self, db, task_id, kind, detail=None):
        db.execute("INSERT INTO events(task_id,ts,kind,detail) VALUES(?,?,?,?)",
                   (task_id, self.now(), kind, json.dumps(detail, default=str) if detail is not None else None))

    # ---- tasks -------------------------------------------------------------
    def create_task(self, *, worker, task_type, inputs, dedupe_key, business_application,
                    substream, priority=50, depends_on=(), mode="none", paid=False,
                    max_attempts=3, input_fingerprint=None):
        with self.tx() as db:
            row = db.execute("SELECT id FROM tasks WHERE dedupe_key=? AND state IN "
                             "('pending','running','blocked','completed')", (dedupe_key,)).fetchone()
            if row:
                self.event(db, row["id"], "duplicate_suppressed", {"dedupe_key": dedupe_key})
                return row["id"], False
            tid = "task-" + uuid.uuid4().hex[:16]
            now = self.now()
            db.execute(
                "INSERT INTO tasks(id,dedupe_key,business_application,substream,worker,task_type,priority,"
                "state,depends_on,inputs,input_fingerprint,max_attempts,created_at,updated_at,mode,paid)"
                " VALUES(?,?,?,?,?,?,?,'pending',?,?,?,?,?,?,?,?)",
                (tid, dedupe_key, business_application, substream, worker, task_type, priority,
                 json.dumps(list(depends_on)), json.dumps(inputs, default=str),
                 input_fingerprint or fingerprint(inputs), max_attempts, now, now, mode, int(paid)))
            self.event(db, tid, "created", {"worker": worker, "type": task_type})
            return tid, True

    def get(self, task_id):
        db = self._connect()
        try:
            row = db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            return dict(row) if row else None
        finally:
            db.close()

    def list_tasks(self, states=None):
        db = self._connect()
        try:
            rows = db.execute("SELECT * FROM tasks ORDER BY priority DESC, created_at").fetchall()
            return [dict(r) for r in rows if not states or r["state"] in states]
        finally:
            db.close()

    def claim(self, task_id, owner, lease_seconds):
        """Atomically move pending -> running with a lease. False if another claimer won."""
        from datetime import timedelta
        with self.tx() as db:
            expires = iso(self.clock() + timedelta(seconds=lease_seconds))
            cur = db.execute(
                "UPDATE tasks SET state='running', attempts=attempts+1, lease_owner=?, lease_expires=?,"
                " started_at=?, updated_at=?, blocking_reason=NULL WHERE id=? AND state='pending'",
                (owner, expires, self.now(), self.now(), task_id))
            if cur.rowcount == 1:
                self.event(db, task_id, "claimed", {"owner": owner})
            return cur.rowcount == 1

    def checkpoint(self, task_id, owner, data, lease_seconds=None):
        from datetime import timedelta
        with self.tx() as db:
            expires = iso(self.clock() + timedelta(seconds=lease_seconds)) if lease_seconds else None
            cur = db.execute(
                "UPDATE tasks SET checkpoint=?, updated_at=?, lease_expires=COALESCE(?,lease_expires)"
                " WHERE id=? AND state='running' AND lease_owner=?",
                (json.dumps(data, default=str), self.now(), expires, task_id, owner))
            if cur.rowcount:
                self.event(db, task_id, "checkpoint", None)
            return cur.rowcount == 1

    def finish(self, task_id, owner, state, *, outputs=None, evidence=None, blocking_reason=None,
               requires_human=False, error=None):
        if state not in ("completed", "blocked", "failed", "pending", "cancelled"):
            raise ValueError(state)
        with self.tx() as db:
            row = db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            if not row or row["state"] != "running" or row["lease_owner"] != owner:
                return False
            terminal = state in ("completed", "failed", "cancelled")
            db.execute(
                "UPDATE tasks SET state=?, outputs=?, evidence=?, blocking_reason=?, requires_human=?, error=?,"
                " lease_owner=NULL, lease_expires=NULL, updated_at=?, finished_at=? WHERE id=?",
                (state, json.dumps(outputs, default=str) if outputs is not None else row["outputs"],
                 json.dumps(evidence if evidence is not None else json.loads(row["evidence"]), default=str),
                 blocking_reason, int(requires_human), error, self.now(),
                 self.now() if terminal else None, task_id))
            self.event(db, task_id, "finished_" + state, {"reason": blocking_reason, "error": error})
            return True

    def set_blocked(self, task_id, reason, requires_human=False):
        """Block a pending task without running it (e.g. worker not implemented)."""
        with self.tx() as db:
            cur = db.execute("UPDATE tasks SET state='blocked', blocking_reason=?, requires_human=?, updated_at=?"
                             " WHERE id=? AND state IN ('pending','blocked')",
                             (reason, int(requires_human), self.now(), task_id))
            if cur.rowcount:
                self.event(db, task_id, "blocked", {"reason": reason})
            return cur.rowcount == 1

    def unblock(self, task_id):
        with self.tx() as db:
            cur = db.execute("UPDATE tasks SET state='pending', blocking_reason=NULL, updated_at=?"
                             " WHERE id=? AND state='blocked'", (self.now(), task_id))
            if cur.rowcount:
                self.event(db, task_id, "unblocked", None)
            return cur.rowcount == 1

    def cancel(self, task_id, reason="cancelled by operator"):
        with self.tx() as db:
            cur = db.execute("UPDATE tasks SET state='cancelled', blocking_reason=?, lease_owner=NULL,"
                             " lease_expires=NULL, updated_at=?, finished_at=? WHERE id=? AND state IN "
                             "('pending','blocked','running')", (reason, self.now(), self.now(), task_id))
            if cur.rowcount:
                self.event(db, task_id, "cancelled", {"reason": reason})
            return cur.rowcount == 1

    def recover_expired(self):
        """Safe restart: running tasks whose lease lapsed return to pending (or fail at max attempts)."""
        recovered = []
        with self.tx() as db:
            rows = db.execute("SELECT id,attempts,max_attempts FROM tasks WHERE state='running' "
                              "AND lease_expires < ?", (self.now(),)).fetchall()
            for r in rows:
                if r["attempts"] >= r["max_attempts"]:
                    db.execute("UPDATE tasks SET state='failed', error='lease expired at max attempts',"
                               " lease_owner=NULL, lease_expires=NULL, finished_at=?, updated_at=? WHERE id=?",
                               (self.now(), self.now(), r["id"]))
                    self.event(db, r["id"], "recovered_failed", None)
                else:
                    db.execute("UPDATE tasks SET state='pending', lease_owner=NULL, lease_expires=NULL,"
                               " updated_at=? WHERE id=?", (self.now(), r["id"]))
                    self.event(db, r["id"], "recovered_to_pending", {"attempts": r["attempts"]})
                recovered.append(r["id"])
        return recovered

    def add_task_cost(self, task_id, estimated=0.0, confirmed=0.0, usage_record=None):
        with self.tx() as db:
            row = db.execute("SELECT model_usage FROM tasks WHERE id=?", (task_id,)).fetchone()
            usage = json.loads(row["model_usage"])
            if usage_record:
                usage.append(usage_record)
            db.execute("UPDATE tasks SET cost_estimated_usd=cost_estimated_usd+?, cost_confirmed_usd="
                       "cost_confirmed_usd+?, model_usage=?, updated_at=? WHERE id=?",
                       (estimated, confirmed, json.dumps(usage), self.now(), task_id))

    # ---- locks, control ----------------------------------------------------
    def acquire_lock(self, name, owner, ttl_seconds):
        from datetime import timedelta
        with self.tx() as db:
            row = db.execute("SELECT * FROM locks WHERE name=?", (name,)).fetchone()
            now = self.now()
            if row and row["expires"] > now and row["owner"] != owner:
                return False
            db.execute("INSERT OR REPLACE INTO locks VALUES(?,?,?)",
                       (name, owner, iso(self.clock() + timedelta(seconds=ttl_seconds))))
            return True

    def release_lock(self, name, owner):
        with self.tx() as db:
            db.execute("DELETE FROM locks WHERE name=? AND owner=?", (name, owner))

    def set_control(self, key, value):
        with self.tx() as db:
            db.execute("INSERT OR REPLACE INTO control VALUES(?,?,?)", (key, str(value), self.now()))

    def get_control(self, key, default=None):
        db = self._connect()
        try:
            row = db.execute("SELECT value FROM control WHERE key=?", (key,)).fetchone()
            return row["value"] if row else default
        finally:
            db.close()

    def set_meta(self, key, value):
        with self.tx() as db:
            db.execute("INSERT OR REPLACE INTO meta VALUES(?,?)", (key, json.dumps(value, default=str)))

    def get_meta(self, key, default=None):
        db = self._connect()
        try:
            row = db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
            return json.loads(row["value"]) if row else default
        finally:
            db.close()

    def record_run(self, kind, ok, detail=None):
        with self.tx() as db:
            db.execute("INSERT INTO runs(ts,kind,ok,detail) VALUES(?,?,?,?)",
                       (self.now(), kind, int(ok), json.dumps(detail, default=str)))

    def query(self, sql, params=()):
        db = self._connect()
        try:
            return [dict(r) for r in db.execute(sql, params).fetchall()]
        finally:
            db.close()
