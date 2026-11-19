"""Persistence (SQLite): plan versions, change proposals waiting for approval, hash-chained audit log."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
from datetime import datetime, timezone

SCHEMA = """
CREATE TABLE IF NOT EXISTS scenario (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at TEXT NOT NULL,
  parent_id INTEGER,
  status TEXT NOT NULL,           -- active | superseded | proposed | rejected | baseline
  label TEXT NOT NULL,
  problem_json TEXT NOT NULL,
  plan_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS proposal (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at TEXT NOT NULL,
  base_id INTEGER NOT NULL,
  new_id INTEGER NOT NULL,
  change_json TEXT NOT NULL,
  diff_json TEXT NOT NULL,
  explanation TEXT NOT NULL,
  status TEXT NOT NULL,           -- PENDING | APPLIED | REJECTED | STALE
  source TEXT NOT NULL,           -- rules | llm | api
  created_by TEXT NOT NULL,
  decided_by TEXT,
  decided_at TEXT
);
CREATE TABLE IF NOT EXISTS audit (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL,
  actor TEXT NOT NULL,
  action TEXT NOT NULL,
  detail TEXT NOT NULL,
  prev_hash TEXT NOT NULL,
  hash TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS llm_call (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL,
  model TEXT NOT NULL,
  input_tokens INTEGER, output_tokens INTEGER, cache_read_tokens INTEGER,
  cost_usd REAL, latency_ms INTEGER
);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, path: str | None = None):
        self.path = path or os.environ.get("DISPATCHER_DB", "dispatcher.db")
        self._lock = threading.RLock()
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)

    # ------------------------------------------------------------------------------------- scenarios
    def add_scenario(self, label: str, problem: dict, plan: dict, status: str, parent_id: int | None = None) -> int:
        with self._lock:
            cur = self.conn.execute(
                "INSERT INTO scenario(created_at,parent_id,status,label,problem_json,plan_json) VALUES (?,?,?,?,?,?)",
                (now(), parent_id, status, label, json.dumps(problem, ensure_ascii=False), json.dumps(plan, ensure_ascii=False)))
            self.conn.commit()
            return cur.lastrowid

    def scenario(self, sid: int) -> dict | None:
        row = self.conn.execute("SELECT * FROM scenario WHERE id=?", (sid,)).fetchone()
        return self._scn(row)

    def active(self) -> dict | None:
        row = self.conn.execute("SELECT * FROM scenario WHERE status='active' ORDER BY id DESC LIMIT 1").fetchone()
        return self._scn(row)

    def baseline(self) -> dict | None:
        row = self.conn.execute("SELECT * FROM scenario WHERE status='baseline' ORDER BY id DESC LIMIT 1").fetchone()
        return self._scn(row)

    def set_status(self, sid: int, status: str) -> None:
        with self._lock:
            self.conn.execute("UPDATE scenario SET status=? WHERE id=?", (status, sid))
            self.conn.commit()

    def history(self) -> list[dict]:
        rows = self.conn.execute("SELECT id,created_at,parent_id,status,label FROM scenario "
                                 "WHERE status IN ('active','superseded') ORDER BY id").fetchall()
        return [dict(r) for r in rows]

    @staticmethod
    def _scn(row) -> dict | None:
        if row is None:
            return None
        d = dict(row)
        d["problem"] = json.loads(d.pop("problem_json"))
        d["plan"] = json.loads(d.pop("plan_json"))
        return d

    # ------------------------------------------------------------------------------------- proposals
    def add_proposal(self, base_id: int, new_id: int, change: dict, diff: dict, explanation: str,
                     source: str, created_by: str) -> int:
        with self._lock:
            cur = self.conn.execute(
                "INSERT INTO proposal(created_at,base_id,new_id,change_json,diff_json,explanation,status,source,created_by)"
                " VALUES (?,?,?,?,?,?, 'PENDING', ?, ?)",
                (now(), base_id, new_id, json.dumps(change, ensure_ascii=False), json.dumps(diff, ensure_ascii=False),
                 explanation, source, created_by))
            self.conn.commit()
            return cur.lastrowid

    def proposal(self, pid: int) -> dict | None:
        row = self.conn.execute("SELECT * FROM proposal WHERE id=?", (pid,)).fetchone()
        if row is None:
            return None
        d = dict(row)
        d["change"] = json.loads(d.pop("change_json"))
        d["diff"] = json.loads(d.pop("diff_json"))
        return d

    def proposals(self, status: str | None = None, limit: int = 50) -> list[dict]:
        q = "SELECT id FROM proposal" + (" WHERE status=?" if status else "") + " ORDER BY id DESC LIMIT ?"
        args = (status, limit) if status else (limit,)
        return [self.proposal(r["id"]) for r in self.conn.execute(q, args).fetchall()]

    def decide(self, pid: int, status: str, user: str) -> None:
        with self._lock:
            self.conn.execute("UPDATE proposal SET status=?, decided_by=?, decided_at=? WHERE id=?",
                              (status, user, now(), pid))
            self.conn.commit()

    # ----------------------------------------------------------------------------------------- audit
    def audit(self, actor: str, action: str, detail: dict) -> None:
        with self._lock:
            row = self.conn.execute("SELECT hash FROM audit ORDER BY id DESC LIMIT 1").fetchone()
            prev = row["hash"] if row else "0" * 64
            ts = now()
            body = json.dumps(detail, ensure_ascii=False, sort_keys=True)
            h = hashlib.sha256(f"{prev}|{ts}|{actor}|{action}|{body}".encode()).hexdigest()
            self.conn.execute("INSERT INTO audit(ts,actor,action,detail,prev_hash,hash) VALUES (?,?,?,?,?,?)",
                              (ts, actor, action, body, prev, h))
            self.conn.commit()

    def audit_log(self, limit: int = 200) -> list[dict]:
        rows = self.conn.execute("SELECT * FROM audit ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [{**dict(r), "detail": json.loads(r["detail"])} for r in rows]

    def verify_audit(self) -> tuple[bool, int | None]:
        prev = "0" * 64
        for r in self.conn.execute("SELECT * FROM audit ORDER BY id").fetchall():
            h = hashlib.sha256(f"{prev}|{r['ts']}|{r['actor']}|{r['action']}|{r['detail']}".encode()).hexdigest()
            if r["prev_hash"] != prev or r["hash"] != h:
                return False, r["id"]
            prev = r["hash"]
        return True, None

    # ------------------------------------------------------------------------------------------- llm
    def log_llm(self, model: str, input_tokens: int, output_tokens: int, cache_read: int, cost: float,
                latency_ms: int) -> None:
        with self._lock:
            self.conn.execute("INSERT INTO llm_call(ts,model,input_tokens,output_tokens,cache_read_tokens,cost_usd,"
                              "latency_ms) VALUES (?,?,?,?,?,?,?)",
                              (now(), model, input_tokens, output_tokens, cache_read, cost, latency_ms))
            self.conn.commit()

    def reset(self) -> None:
        with self._lock:
            for t in ("scenario", "proposal", "audit", "llm_call"):
                self.conn.execute(f"DELETE FROM {t}")
            self.conn.execute("DELETE FROM sqlite_sequence")
            self.conn.commit()
