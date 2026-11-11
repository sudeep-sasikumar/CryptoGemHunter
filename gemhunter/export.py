"""Research files: one gzip-compressed JSON-lines file per UTC day with every graded snapshot of that day
(all factors + what happened next), that day's calls with their full timeline, and the smart-money / signal
events seen. Served by the dashboard (/api/research), copied to the PC by tools/sync_research.py, studied by
tools/analyze.py. Holds token addresses and market data only: no keys, no passwords."""
from __future__ import annotations

import calendar
import gzip
import io
import json
import re
import sqlite3
import time
from pathlib import Path

NAME = re.compile(r"(\d{4}-\d{2}-\d{2})\.jsonl\.gz")


def day_bounds(day: str) -> tuple[float, float]:
    start = float(calendar.timegm(time.strptime(day, "%Y-%m-%d")))
    return start, start + 86400


def _ro(db_path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=30)
    con.row_factory = sqlite3.Row
    return con


def list_days(db_path: Path) -> list[dict]:
    con = _ro(db_path)
    try:
        snaps = dict(con.execute("SELECT date(ts, 'unixepoch') d, COUNT(*) FROM snapshots WHERE done=1 GROUP BY d").fetchall())
        calls = dict(con.execute("SELECT date(ts, 'unixepoch') d, COUNT(*) FROM calls GROUP BY d").fetchall())
    finally:
        con.close()
    return [{"name": f"{d}.jsonl.gz", "snapshots": snaps.get(d, 0), "calls": calls.get(d, 0)}
            for d in sorted(set(snaps) | set(calls)) if d]


def build_day(db_path: Path, day: str) -> bytes:
    """Runs in a worker thread with its own read-only connection."""
    start, end = day_bounds(day)
    con = _ro(db_path)
    layouts = {r["id"]: json.loads(r["keys"]) for r in con.execute("SELECT id, keys FROM feature_keys")}
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb", compresslevel=6, mtime=0) as gz:
        def put(obj):
            gz.write((json.dumps(obj, separators=(",", ":"), default=str) + "\n").encode())
        put({"kind": "meta", "day": day, "exported_at": time.time(), "format": 1})
        for r in con.execute(
                "SELECT s.id, s.token_id, s.ts, s.stage, s.price, s.entry_price, s.mcap, s.liq, s.fkeys, s.features, s.score, "
                "s.prob, s.model_ver, s.gate, s.called, s.hit, s.stopped, s.peak_mult, s.min_mult, s.end_mult, s.peak_mcap, "
                "s.t_hit, s.t_stop, s.t_peak, s.end_reason, s.fast_hit, s.t_fast, t.chain, t.symbol, t.name, t.launchpad, t.creator "
                "FROM snapshots s LEFT JOIN tokens t ON t.id = s.token_id WHERE s.ts >= ? AND s.ts < ? AND s.done = 1",
                (start, end)):
            row = dict(r)
            vals = json.loads(row.pop("features") or "null")
            keys = layouts.get(row.pop("fkeys"))
            row["f"] = dict(zip(keys, vals)) if keys and isinstance(vals, list) else (vals or {})
            put({"kind": "snapshot", **row})
        for r in con.execute("SELECT c.*, t.chain, t.symbol, t.address, t.launchpad FROM calls c LEFT JOIN tokens t "
                             "ON t.id = c.token_id WHERE c.ts >= ? AND c.ts < ?", (start, end)):
            row = dict(r)
            row["reasons"] = json.loads(row["reasons"] or "{}")
            row.pop("tg_msg", None)
            row.pop("state", None)
            row["events"] = [dict(e) for e in con.execute(
                "SELECT ts, kind, label, price, mcap, mult, text FROM call_events WHERE call_id=? ORDER BY ts", (row["id"],))]
            put({"kind": "call", **row})
        for r in con.execute("SELECT * FROM gm_signals WHERE seen_ts >= ? AND seen_ts < ?", (start, end)):
            put({"kind": "signal", **dict(r)})
    con.close()
    return buf.getvalue()
