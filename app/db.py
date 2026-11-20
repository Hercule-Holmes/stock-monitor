"""SQLite 连接与建表"""
import sqlite3
import threading

from . import config

_local = threading.local()

SCHEMA = """
CREATE TABLE IF NOT EXISTS stocks (
  code TEXT PRIMARY KEY,
  name TEXT,
  market TEXT,
  close REAL, pct_chg REAL, turnover REAL, volume_ratio REAL,
  ma5 REAL, ma10 REAL, ma20 REAL, ma60 REAL, ma120 REAL, ma250 REAL,
  boll_up REAL, boll_mid REAL, boll_low REAL,
  below_ma20 INTEGER, below_ma60 INTEGER, below_ma120 INTEGER, below_ma250 INTEGER,
  below_boll_mid INTEGER, below_boll_low INTEGER, above_boll_up INTEGER,
  pe_ttm REAL, pe_static REAL, pb REAL, psr REAL, pcr REAL, peg REAL,
  mktcap REAL, floatcap REAL,
  dv_ttm REAL,
  px_pct REAL, pe_pct REAL, pb_pct REAL,
  daily_date TEXT, val_date TEXT, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS daily (
  code TEXT, date TEXT,
  open REAL, high REAL, low REAL, close REAL,
  volume REAL, amount REAL, pct_chg REAL,
  PRIMARY KEY (code, date)
);
CREATE TABLE IF NOT EXISTS valuation (
  code TEXT, date TEXT, close REAL,
  pe_ttm REAL, pe_static REAL, pb REAL, peg REAL, pcr REAL, psr REAL,
  mktcap REAL,
  PRIMARY KEY (code, date)
);
CREATE TABLE IF NOT EXISTS dividends (
  code TEXT, ex_date TEXT, cash REAL,
  PRIMARY KEY (code, ex_date)
);
CREATE TABLE IF NOT EXISTS pools (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT UNIQUE NOT NULL,
  note TEXT,
  created_at TEXT
);
CREATE TABLE IF NOT EXISTS pool_stocks (
  pool_id INTEGER NOT NULL,
  code TEXT NOT NULL,
  tag TEXT, note TEXT, target_price REAL,
  added_at TEXT,
  PRIMARY KEY (pool_id, code)
);
CREATE TABLE IF NOT EXISTS rules (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT,
  indicator TEXT NOT NULL,
  op TEXT NOT NULL,
  value REAL,
  pool_id INTEGER,
  email INTEGER DEFAULT 0,
  enabled INTEGER DEFAULT 1,
  created_at TEXT
);
CREATE TABLE IF NOT EXISTS signals (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  rule_id INTEGER NOT NULL,
  code TEXT NOT NULL,
  trade_date TEXT NOT NULL,
  detail TEXT,
  emailed INTEGER DEFAULT 0,
  created_at TEXT,
  UNIQUE (rule_id, code, trade_date)
);
CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value TEXT
);
CREATE INDEX IF NOT EXISTS idx_daily_date ON daily (date);
CREATE INDEX IF NOT EXISTS idx_signals_date ON signals (trade_date);
"""


def get_db() -> sqlite3.Connection:
    """线程内复用连接"""
    conn = getattr(_local, "conn", None)
    if conn is None:
        conn = sqlite3.connect(config.DB_PATH, timeout=config.DB_TIMEOUT,
                               check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA foreign_keys=ON")
        _local.conn = conn
    return conn


def init_db():
    conn = get_db()
    conn.executescript(SCHEMA)
    conn.commit()


def query(sql: str, args: tuple = ()) -> list:
    return get_db().execute(sql, args).fetchall()


def execute(sql: str, args: tuple = ()) -> None:
    conn = get_db()
    conn.execute(sql, args)
    conn.commit()


def get_setting(key: str, default: str = "") -> str:
    rows = query("SELECT value FROM settings WHERE key=?", (key,))
    return rows[0]["value"] if rows else default


def set_setting(key: str, value: str):
    execute("INSERT INTO settings(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))
