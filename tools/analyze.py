r"""What the tokens that ran had in common - a plain-language report from the research files that
tools/sync_research.py copied from the VPS (var/research/vps/*.jsonl.gz).

    .venv\Scripts\python.exe tools\analyze.py                 every day on this PC
    .venv\Scripts\python.exe tools\analyze.py --days 14       the newest 14 days
    .venv\Scripts\python.exe tools\analyze.py --stage deep    only snapshots that had the deep look

It answers: how often did a snapshot hit; which chains and launchpads the winners came from; whether the
scorecard ranks winners on top; which factor ranges winners sat in (and whether that held on the newer half
of the data, which the ranges were not chosen on); and how the calls did.
"""
from __future__ import annotations

import argparse
import gzip
import json
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from gemhunter.config import load_config  # noqa: E402
from gemhunter.export import NAME  # noqa: E402


def load(src: Path, days: int | None) -> tuple[list[dict], list[dict]]:
    files = sorted(p for p in src.glob("*.jsonl.gz") if NAME.fullmatch(p.name))
    if days:
        files = files[-days:]
    snaps, calls = [], []
    for p in files:
        try:
            with gzip.open(p, "rt", encoding="utf-8") as fh:
                for line in fh:
                    row = json.loads(line)
                    (snaps if row.get("kind") == "snapshot" else calls if row.get("kind") == "call" else []).append(row)
        except (OSError, EOFError, ValueError):
            continue
    snaps.sort(key=lambda r: r["ts"])
    return snaps, calls


def rate(rows: list[dict]) -> float:
    return sum(1 for r in rows if r.get("hit")) / len(rows) if rows else 0.0


def by(rows: list[dict], key) -> list[tuple[str, int, float, float]]:
    groups: dict[str, list[dict]] = {}
    for r in rows:
        groups.setdefault(str(key(r) or "?"), []).append(r)
    return sorted(((k, len(v), rate(v), statistics.mean(x.get("peak_mult") or 1 for x in v)) for k, v in groups.items()),
                  key=lambda t: -t[1])


def factor_ranges(train: list[dict], test: list[dict], bins: int = 4, top: int = 25) -> list[dict]:
    """Per factor: the value range with the best hit rate on the older half, then the hit rate of that same
    range on the newer half - an honest check, since the range was not picked on it."""
    base_a, base_b = rate(train), rate(test)
    names: dict[str, int] = {}
    for r in train:
        for k, v in (r.get("f") or {}).items():
            if isinstance(v, (int, float)):
                names[k] = names.get(k, 0) + 1
    out = []
    for k, n in names.items():
        if n < max(80, 0.15 * len(train)):
            continue
        vals = sorted(r["f"][k] for r in train if isinstance(r["f"].get(k), (int, float)))
        edges = sorted({vals[int(i * (len(vals) - 1) / bins)] for i in range(bins + 1)})
        if len(edges) < 3:
            continue
        best = None
        for lo, hi in zip(edges, edges[1:]):
            inside = lambda r, lo=lo, hi=hi: isinstance(r["f"].get(k), (int, float)) and lo <= r["f"][k] <= hi   # noqa: E731
            a = [r for r in train if inside(r)]
            if len(a) >= 40 and (best is None or rate(a) > best[2]):
                best = (lo, hi, rate(a), len(a), inside)
        if not best or best[2] < 1.5 * base_a:
            continue
        b = [r for r in test if best[4](r)]
        out.append({"factor": k, "lo": best[0], "hi": best[1], "older": best[2], "n_older": best[3],
                    "newer": rate(b) if len(b) >= 20 else None, "n_newer": len(b),
                    "held": len(b) >= 20 and rate(b) >= 1.25 * base_b})
    out.sort(key=lambda d: (-int(d["held"]), -(d["newer"] or 0)))
    return out[:top]


def write_report(src: Path, out: Path, days: int | None = None, stage: str | None = None) -> None:
    snaps, calls = load(src, days)
    if stage:
        snaps = [s for s in snaps if s.get("stage") == stage]
    L = [f"# Gem Hunter - what the runners had in common ({time.strftime('%Y-%m-%d')})", ""]
    if not snaps:
        L.append("No graded snapshots on this PC yet. Outcomes take up to 7 days; run tools/sync_research.py again later.")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("\n".join(L), encoding="utf-8")
        return
    pc = lambda x: f"{x * 100:.1f}%"   # noqa: E731
    L += [f"{len(snaps):,} graded snapshots from {time.strftime('%Y-%m-%d', time.gmtime(snaps[0]['ts']))} to "
          f"{time.strftime('%Y-%m-%d', time.gmtime(snaps[-1]['ts']))}; {pc(rate(snaps))} were a hit.", "",
          "## Peak multiples", "", "| Reached | Snapshots | Share |", "|---|---|---|"]
    for m in (2, 3, 5, 10, 20):
        n = sum(1 for s in snaps if (s.get("peak_mult") or 0) >= m)
        L.append(f"| {m}x | {n:,} | {pc(n / len(snaps))} |")
    for title, key in (("By chain", lambda r: r.get("chain")), ("By launchpad", lambda r: r.get("launchpad")),
                       ("By stage", lambda r: r.get("stage"))):
        L += ["", f"## {title}", "", "| | Snapshots | Hit | Average peak |", "|---|---|---|---|"]
        L += [f"| {k} | {n:,} | {pc(r)} | {p:.2f}x |" for k, n, r, p in by(snaps, key)[:15]]
    L += ["", "## Does the scorecard rank winners on top?", "", "| Score | Snapshots | Hit |", "|---|---|---|"]
    for lo in range(30, 100, 10):
        b = [s for s in snaps if lo <= (s.get("score") or 0) < lo + 10]
        if b:
            L.append(f"| {lo}-{lo + 10} | {len(b):,} | {pc(rate(b))} |")
    gated, clean = [s for s in snaps if s.get("gate")], [s for s in snaps if not s.get("gate")]
    L += ["", f"Failed a gate: {len(gated):,} snapshots, {pc(rate(gated))} hit. Passed every gate: {len(clean):,}, {pc(rate(clean))} hit."]
    half = len(snaps) // 2
    ranges = factor_ranges(snaps[:half], snaps[half:]) if half >= 200 else []
    L += ["", "## Factor ranges the winners sat in", "",
          "Picked on the older half of the data, then checked on the newer half. \"Held\" means it still hit at least "
          "1.25x the average there.", ""]
    if ranges:
        L += ["| Factor | Range | Hit (older half) | Hit (newer half) | Held |", "|---|---|---|---|---|"]
        L += [f"| {d['factor']} | {d['lo']:.4g} to {d['hi']:.4g} | {pc(d['older'])} ({d['n_older']}) | "
              f"{pc(d['newer']) if d['newer'] is not None else 'too few'} ({d['n_newer']}) | {'yes' if d['held'] else 'no'} |"
              for d in ranges]
    else:
        L.append("Not enough graded snapshots yet (needs 400).")
    closed = [c for c in calls if c.get("status") == "closed"]
    L += ["", "## Calls", ""]
    if closed:
        wins = sum(1 for c in closed if (c.get("exit_mult") or 0) >= 1)
        L += [f"{len(calls)} calls, {len(closed)} closed: {wins} in profit, average result "
              f"{statistics.mean(c.get('exit_mult') or 0 for c in closed):.2f}x, best peak "
              f"{max(c.get('peak_mult') or 0 for c in calls):.1f}x.", "", "| Exit reason | Calls | Average result |", "|---|---|---|"]
        reasons: dict[str, list[float]] = {}
        for c in closed:
            reasons.setdefault(str(c.get("exit_reason")), []).append(c.get("exit_mult") or 0)
        L += [f"| {k} | {len(v)} | {statistics.mean(v):.2f}x |" for k, v in sorted(reasons.items(), key=lambda t: -len(t[1]))]
    else:
        L.append(f"{len(calls)} calls, none closed yet.")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(L) + "\n", encoding="utf-8")


def default_out(cfg) -> Path:
    return cfg.data_dir / "research" / f"report-{time.strftime('%Y-%m-%d')}.md"


def main() -> int:
    cfg = load_config()
    ap = argparse.ArgumentParser(description="Traits report from the synced research files")
    ap.add_argument("--src", default=str(cfg.data_dir / "research" / "vps"))
    ap.add_argument("--days", type=int, default=None)
    ap.add_argument("--stage", choices=["scan", "deep"], default=None)
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    out = Path(a.out) if a.out else default_out(cfg)
    write_report(Path(a.src), out, a.days, a.stage)
    print(f"report written: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
