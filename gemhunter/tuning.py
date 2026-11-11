"""The scorecard tunes itself from the first hours on - long before a learned model can be trained.

Every snapshot gets a FAST grade (label.fast: 1.5x before -30% within 24 hours by default) besides the full one
(3x before -50% within 7 days). Fast grades arrive within hours. Every few hours, once a handful of winners
exist, each scorecard item is checked against them:

    separation  = how well the item's 0..1 mark ranks winners above the rest (AUC; 0.5 = no better than chance)
    multiplier  = 1 + strength x (2 x AUC - 1) x winners / (winners + prior)        kept inside [min, max]

So an item that keeps pointing at winners gains weight inside its group, one that points the wrong way loses
it, and with only 5 winners the change is deliberately small (the prior stands in for 30 winners of doubt).
Groups are re-weighted the same way, around the spec's priority order. Nothing is ever inverted or dropped,
the gates are not touched, and the learned model (model.py) still has to beat this tuned scorecard on unseen
data before it takes over.
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

from .scoring import ITEMS


def auc(pos: list[float], neg: list[float]) -> float | None:
    """Chance that a winner's mark is above a non-winner's (ties count half)."""
    if not pos or not neg:
        return None
    ranked = sorted([(v, 1) for v in pos] + [(v, 0) for v in neg])
    rank_sum, i = 0.0, 0
    while i < len(ranked):
        j = i
        while j < len(ranked) and ranked[j][0] == ranked[i][0]:
            j += 1
        mid = (i + j + 1) / 2.0                          # average rank of the tied block (1-based)
        rank_sum += mid * sum(1 for k in range(i, j) if ranked[k][1])
        i = j
    return (rank_sum - len(pos) * (len(pos) + 1) / 2.0) / (len(pos) * len(neg))


def multiplier(a: float | None, n_pos: int, cfg, lo: float, hi: float) -> float:
    if a is None:
        return 1.0
    m = 1.0 + float(cfg["strength"]) * (2 * a - 1) * n_pos / (n_pos + float(cfg["prior_winners"]))
    return round(max(lo, min(hi, m)), 3)


def tune(db_path: Path, weights: dict, cfg: dict) -> dict:
    """Runs in a worker thread with its own read-only connection. Returns the new tuning, or a note on why not yet."""
    since = time.time() - float(cfg["window_days"]) * 86400
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=30)
    try:
        layouts = {i: json.loads(k) for i, k in con.execute("SELECT id, keys FROM feature_keys")}
        rows = con.execute("SELECT fkeys, features, fast_hit, hit, done FROM snapshots WHERE ts>? AND (fast_hit IS NOT NULL OR done=1) "
                           "ORDER BY ts DESC LIMIT 60000", (since,)).fetchall()
    finally:
        con.close()
    ys, marks = [], []
    for fkeys, feats, fast, hit, done in rows:
        vals = json.loads(feats or "null")
        f = vals if isinstance(vals, dict) else dict(zip(layouts.get(fkeys, ()), vals or []))
        ys.append(int(bool(fast) or bool(done and hit)))              # a winner on either grade
        row = []
        for _, _, fn in ITEMS:
            try:
                row.append(fn(f))
            except (TypeError, ValueError, ZeroDivisionError):
                row.append(None)
        marks.append(row)
    n, pos = len(ys), sum(ys)
    out: dict = {"ts": time.time(), "n": n, "pos": pos, "items": {}, "groups": {}}
    if pos < int(cfg["min_winners"]) or n - pos < int(cfg["min_rows"]) - int(cfg["min_winners"]):
        out["note"] = f"collecting: {pos}/{cfg['min_winners']} winners, {n}/{cfg['min_rows']} graded snapshots"
        return out
    group_marks: dict[str, list[list[float]]] = {g: [[] for _ in range(n)] for g in weights}
    for j, (group, label, _) in enumerate(ITEMS):
        p = [m[j] for m, y in zip(marks, ys) if y and m[j] is not None]
        q = [m[j] for m, y in zip(marks, ys) if not y and m[j] is not None]
        a = auc(p, q) if len(p) >= 3 and len(q) >= 20 else None
        out["items"][label] = {"group": group, "auc": None if a is None else round(a, 3), "n": len(p) + len(q), "pos": len(p),
                               "mult": multiplier(a, len(p), cfg, float(cfg["item_min"]), float(cfg["item_max"]))}
        if group in group_marks:
            for i, m in enumerate(marks):
                if m[j] is not None:
                    group_marks[group][i].append(m[j])
    for g, per_row in group_marks.items():
        means = [(sum(v) / len(v), y) for v, y in zip(per_row, ys) if v]
        p, q = [m for m, y in means if y], [m for m, y in means if not y]
        a = auc(p, q) if len(p) >= 3 and len(q) >= 20 else None
        out["groups"][g] = {"auc": None if a is None else round(a, 3), "n": len(means), "pos": len(p),
                            "mult": multiplier(a, len(p), cfg, float(cfg["group_min"]), float(cfg["group_max"]))}
    out["note"] = f"tuned on {n} graded snapshots, {pos} winners"
    return out
