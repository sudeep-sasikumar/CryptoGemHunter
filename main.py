"""Gem Hunter - self-learning tracker of micro-cap tokens ($100K-$700K) that could run to multi-million.
Signals only: it never trades and holds no keys.

    python main.py                 run continuously
    python main.py --minutes 5     run for 5 minutes, print what was seen, exit (smoke test)
    python main.py --retrain       retrain now on the stored history, print the report, exit
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import logging.handlers
import os
import sys

from gemhunter.config import load_config
from gemhunter.engine import Engine


def setup_logging(cfg) -> None:
    logdir = cfg.data_dir / "logs"
    logdir.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)-9s %(message)s", "%Y-%m-%d %H:%M:%S")
    root = logging.getLogger()
    root.setLevel(cfg.app.log_level)
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(fmt)
    file = logging.handlers.RotatingFileHandler(logdir / "gemhunter.log", maxBytes=5_000_000, backupCount=3, encoding="utf-8")
    file.setFormatter(fmt)
    root.handlers[:] = [console, file]
    for noisy in ("httpx", "httpcore", "uvicorn.access"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


async def retrain(engine: Engine) -> None:
    report = await engine.retrain(promote=True)
    report.pop("importance", None)
    print(json.dumps(report, indent=1, default=str))
    await engine.gmgn.close()
    await engine.http.close()
    await engine.telegram.close()
    engine.db.close()


def main() -> int:
    # Windows consoles default to cp1252, which cannot print the emoji in alert text.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="Gem Hunter - micro-cap tracker (signals only; never trades)")
    ap.add_argument("--config", default=None, help="path to config.yaml")
    ap.add_argument("--minutes", type=float, default=None, help="run this long, then print counters and exit")
    ap.add_argument("--retrain", action="store_true", help="retrain on stored history and exit")
    args = ap.parse_args()

    cfg = load_config(args.config)
    setup_logging(cfg)
    print(f"Gem Hunter - version {os.environ.get('APP_VERSION', 'dev')[:7]} - data in {cfg.data_dir}", flush=True)
    engine = Engine(cfg)
    try:
        if args.retrain:
            asyncio.run(retrain(engine))
        else:
            health = {}

            async def go():
                nonlocal health
                task = asyncio.ensure_future(engine.run(once_s=args.minutes * 60 if args.minutes else None))
                if args.minutes:
                    await asyncio.sleep(max(1.0, args.minutes * 60 - 2))
                    health = engine.health()
                await task
            asyncio.run(go())
            if args.minutes:
                print(json.dumps(health, indent=1, default=str))
    except KeyboardInterrupt:
        print("Stopped.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
