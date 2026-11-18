from __future__ import annotations
import sqlite3
from app.config import DB_PATH

SCHEMA = '''
CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS campaigns (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  code TEXT NOT NULL UNIQUE,
  title TEXT NOT NULL,
  face_value REAL NOT NULL,
  min_spend REAL NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS coupon_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  event_type TEXT NOT NULL,
  coupon_code TEXT NOT NULL,
  campaign_code TEXT NOT NULL,
  amount REAL NOT NULL DEFAULT 0,
  business_day TEXT NOT NULL,
  occurred_at TEXT NOT NULL,
  note TEXT NOT NULL DEFAULT '',
  reason_code TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS redeem_facts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  coupon_code TEXT NOT NULL UNIQUE,
  campaign_code TEXT NOT NULL,
  amount REAL NOT NULL,
  business_day TEXT NOT NULL,
  event_id INTEGER NOT NULL,
  batch_id INTEGER,
  status TEXT NOT NULL DEFAULT 'open'
);
CREATE TABLE IF NOT EXISTS redeem_batches (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  business_day TEXT NOT NULL,
  status TEXT NOT NULL,
  fact_count INTEGER NOT NULL DEFAULT 0,
  total_amount REAL NOT NULL DEFAULT 0,
  sealed_at TEXT,
  note TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS redeem_quarantine (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  event_id INTEGER NOT NULL,
  coupon_code TEXT NOT NULL,
  campaign_code TEXT NOT NULL,
  amount REAL NOT NULL DEFAULT 0,
  business_day TEXT NOT NULL,
  occurred_at TEXT NOT NULL,
  reason_code TEXT NOT NULL,
  reason TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'quarantined'
    CHECK (status IN ('quarantined', 'promoted', 'discarded')),
  fact_id INTEGER,
  payload TEXT NOT NULL DEFAULT '{}',
  decision_note TEXT NOT NULL DEFAULT '',
  decided_at TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_quarantine_day_status
  ON redeem_quarantine(business_day, status);
CREATE UNIQUE INDEX IF NOT EXISTS idx_quarantine_event
  ON redeem_quarantine(event_id);
CREATE TABLE IF NOT EXISTS audit_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  action TEXT NOT NULL,
  target_type TEXT NOT NULL,
  target_id INTEGER NOT NULL,
  detail TEXT NOT NULL DEFAULT '',
  actor TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_target
  ON audit_log(target_type, target_id);
'''


def connect(immediate: bool = False) -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    if immediate:
        conn.execute("BEGIN IMMEDIATE")
    return conn


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, ddl: str) -> None:
    cols = {str(r["name"]) for r in conn.execute(f"PRAGMA table_info({table})")}
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


def init_db() -> None:
    conn = connect()
    try:
        conn.executescript(SCHEMA)
        # Older databases predate the quarantine intake, so the reason code is
        # added in place instead of being part of the CREATE TABLE above.
        _ensure_column(conn, "coupon_events", "reason_code", "TEXT NOT NULL DEFAULT ''")
        conn.commit()
    finally:
        conn.close()
