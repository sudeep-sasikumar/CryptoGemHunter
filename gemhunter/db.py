"""SQLite store (WAL). One connection, used only from the event loop thread; the trainer and the
research export open their own read-only connections.

  tokens       every token seen in the market-cap range
  snapshots    every factor at a moment + what happened next      -> the training data
  calls        every call and its trade plan state                -> the scoreboard
  call_events  entry / update / warning / exit of each call       -> the dashboard timeline, Telegram
  bars         thinned price path of followed tokens
  gm_signals, sm_trades   GMGN signal events and smart-money / KOL trades as they were seen
  wallets, token_wallets, deployers   own history: proven wallets and deployer records
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS tokens (
  id TEXT PRIMARY KEY,            -- "<chain>:<address>"
  chain TEXT, address TEXT, symbol TEXT, name TEXT, launchpad TEXT, creator TEXT,
  created_ts REAL, open_ts REAL, first_seen REAL, last_seen REAL,
  first_mcap REAL, peak_mcap REAL, last_mcap REAL, last_price REAL, last_liq REAL,
  meta TEXT, credited INTEGER DEFAULT 0, updated_ts REAL
);
CREATE INDEX IF NOT EXISTS ix_tokens_seen ON tokens(last_seen);

CREATE TABLE IF NOT EXISTS snapshots (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  token_id TEXT, ts REAL, stage TEXT,          -- scan (list data only) / deep (holders, traders, candles ...)
  price REAL, entry_price REAL, mcap REAL, liq REAL,
  fkeys INTEGER, features TEXT,                 -- every factor as known at ts (JSON array in fkeys order)
  score REAL, prob REAL, model_ver TEXT, gate TEXT, called INTEGER DEFAULT 0,
  done INTEGER DEFAULT 0, hit INTEGER DEFAULT 0, stopped INTEGER DEFAULT 0,
  peak_mult REAL DEFAULT 1, min_mult REAL DEFAULT 1, end_mult REAL, peak_mcap REAL,
  t_hit REAL, t_stop REAL, t_peak REAL, end_reason TEXT, done_ts REAL, last_ts REAL,
  fast_hit INTEGER, t_fast REAL                 -- the fast grade (label.fast), known within hours
);
CREATE INDEX IF NOT EXISTS ix_snap_token ON snapshots(token_id);
CREATE INDEX IF NOT EXISTS ix_snap_done ON snapshots(done, ts);

CREATE TABLE IF NOT EXISTS calls (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  snapshot_id INTEGER UNIQUE, token_id TEXT, ts REAL,
  entry_price REAL, entry_mcap REAL, entry_liq REAL,
  score REAL, prob REAL, model_ver TEXT, reasons TEXT, tg_msg TEXT,
  status TEXT DEFAULT 'open',                   -- open / closed
  peak_mult REAL DEFAULT 1, peak_mcap REAL, last_mult REAL DEFAULT 1, last_mcap REAL, last_ts REAL,
  state TEXT,                                   -- trade plan state (JSON): milestones sent, trail armed ...
  exit_ts REAL, exit_price REAL, exit_mult REAL, exit_reason TEXT
);
CREATE INDEX IF NOT EXISTS ix_calls_ts ON calls(ts);

CREATE TABLE IF NOT EXISTS call_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT, call_id INTEGER, ts REAL,
  kind TEXT,                                    -- entry / update / warning / exit
  label TEXT, price REAL, mcap REAL, mult REAL, text TEXT
);
CREATE INDEX IF NOT EXISTS ix_events_call ON call_events(call_id);

CREATE TABLE IF NOT EXISTS bars (token_id TEXT, ts REAL, price REAL, mcap REAL, liq REAL, PRIMARY KEY (token_id, ts)) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS gm_signals (
  id TEXT PRIMARY KEY, chain TEXT, address TEXT, type INTEGER, trigger_at REAL, trigger_mc REAL, first_mc REAL,
  times REAL, seen_ts REAL
);
CREATE INDEX IF NOT EXISTS ix_gm_signals_addr ON gm_signals(address, trigger_at);
CREATE TABLE IF NOT EXISTS sm_trades (
  id TEXT PRIMARY KEY, chain TEXT, kind TEXT,   -- smart / kol
  maker TEXT, address TEXT, side TEXT, usd REAL, price REAL, closing INTEGER, ts REAL
);
CREATE INDEX IF NOT EXISTS ix_sm_addr ON sm_trades(address, ts);

CREATE TABLE IF NOT EXISTS wallets (
  addr TEXT PRIMARY KEY, n_tok INTEGER DEFAULT 0, n_win INTEGER DEFAULT 0, last_ts REAL
);
CREATE TABLE IF NOT EXISTS token_wallets (token_id TEXT, addr TEXT, first_ts REAL, buy_usd REAL,
  PRIMARY KEY (token_id, addr)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS deployers (
  addr TEXT PRIMARY KEY, n_tok INTEGER DEFAULT 0, n_win INTEGER DEFAULT 0, n_dead INTEGER DEFAULT 0,
  n_called INTEGER DEFAULT 0, n_rug INTEGER DEFAULT 0, best_mult REAL DEFAULT 0, last_ts REAL
);

CREATE TABLE IF NOT EXISTS models (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ver TEXT, ts REAL, n_rows INTEGER, n_pos INTEGER,
  metrics TEXT, path TEXT, active INTEGER DEFAULT 0, note TEXT
);
CREATE TABLE IF NOT EXISTS feature_keys (id INTEGER PRIMARY KEY, keys TEXT UNIQUE);
CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS alerts (dedupe TEXT PRIMARY KEY, ts REAL, kind TEXT, text TEXT, msg_id TEXT, status TEXT);
"""


class DB:
    def __init__(self, path: Path):
        self.path = path
        self.con = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
        self.con.row_factory = sqlite3.Row
        if not self.con.execute("SELECT 1 FROM sqlite_master LIMIT 1").fetchone():
            self.con.execute("PRAGMA auto_vacuum=INCREMENTAL")   # only takes effect on a new, empty file
        self.con.execute("PRAGMA journal_mode=WAL")
        self.con.execute("PRAGMA synchronous=NORMAL")
        self.con.execute("PRAGMA busy_timeout=5000")
        self.con.executescript(SCHEMA)
        have = {r["name"] for r in self.con.execute("PRAGMA table_info(snapshots)")}
        for name, kind in (("fast_hit", "INTEGER"), ("t_fast", "REAL")):
            if name not in have:
                self.con.execute(f"ALTER TABLE snapshots ADD COLUMN {name} {kind}")
        self.con.execute("CREATE INDEX IF NOT EXISTS ix_tokens_symbol ON tokens(symbol COLLATE NOCASE)")
        self._in_tx = False
        self._layouts: dict[tuple, int] = {}
        self._keys: dict[int, tuple] = {}

    # -- transactions: begin lazily, commit from the engine tick
    def _begin(self) -> None:
        if not self._in_tx:
            self.con.execute("BEGIN")
            self._in_tx = True

    def commit(self) -> None:
        if self._in_tx:
            self.con.execute("COMMIT")
            self._in_tx = False

    def execute(self, sql: str, args: tuple | dict = ()) -> sqlite3.Cursor:
        self._begin()
        return self.con.execute(sql, args)

    def executemany(self, sql: str, rows) -> None:
        self._begin()
        self.con.executemany(sql, rows)

    def query(self, sql: str, args: tuple | dict = ()) -> list[sqlite3.Row]:
        return self.con.execute(sql, args).fetchall()

    def one(self, sql: str, args: tuple | dict = ()):
        return self.con.execute(sql, args).fetchone()

    def close(self) -> None:
        self.commit()
        self.con.close()

    # -- key/value state
    def get(self, k: str, default=None):
        r = self.one("SELECT v FROM kv WHERE k=?", (k,))
        return json.loads(r["v"]) if r else default

    def put(self, k: str, v) -> None:
        self.execute("INSERT INTO kv(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",
                     (k, json.dumps(v, default=str)))

    # -- compact feature storage: factor names once per layout, values rounded to 4 significant digits
    def pack(self, f: dict) -> tuple[int, str]:
        keys = tuple(sorted(f))
        kid = self._layouts.get(keys)
        if kid is None:
            text = json.dumps(keys)
            r = self.one("SELECT id FROM feature_keys WHERE keys=?", (text,))
            kid = r["id"] if r else self.execute("INSERT INTO feature_keys(keys) VALUES(?)", (text,)).lastrowid
            self._layouts[keys] = kid
            self._keys[kid] = keys
        return kid, json.dumps([_compact(f[k]) for k in keys], separators=(",", ":"), default=str)

    def unpack(self, kid: int | None, text: str | None) -> dict:
        if not text:
            return {}
        vals = json.loads(text)
        if kid is None or isinstance(vals, dict):
            return vals
        keys = self._keys.get(kid)
        if keys is None:
            r = self.one("SELECT keys FROM feature_keys WHERE id=?", (kid,))
            keys = self._keys[kid] = tuple(json.loads(r["keys"])) if r else ()
        return dict(zip(keys, vals))

    # -- maintenance
    def delete_batch(self, table: str, where: str, args: tuple, limit: int = 2000) -> int:
        norowid = {"bars": "token_id, ts", "token_wallets": "token_id, addr"}
        cols = norowid.get(table, "rowid")
        n = self.execute(f"DELETE FROM {table} WHERE ({cols}) IN (SELECT {cols} FROM {table} WHERE {where} LIMIT {limit})",
                         args).rowcount
        self.commit()
        return n

    def size_bytes(self) -> int:
        return sum(p.stat().st_size for p in (self.path, Path(f"{self.path}-wal")) if p.exists())

    def reclaim(self, pages: int = 20000) -> None:
        self.commit()
        self.con.execute(f"PRAGMA incremental_vacuum({pages})").fetchall()
        self.con.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchall()


def _compact(v):
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, float):
        if v != v or v in (float("inf"), float("-inf")):
            return None
        return float(f"{v:.4g}")
    return v
