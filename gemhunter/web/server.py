"""Dashboard: past and open calls with their full timeline, what is being watched, the scoreboard, what the
learner found, system health - and the research files tools/sync_research.py copies to the PC.

Bound to localhost: no login. Bound to anything else (the VPS): DASHBOARD_PASSWORD is required and the
dashboard refuses to start without it. Read-only, except for starting a retrain.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import secrets
import time
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response

from ..export import NAME, build_day, list_days

log = logging.getLogger("web")
STATIC = Path(__file__).parent / "static"
LOCAL = {"127.0.0.1", "localhost", "::1"}
COOKIE = "gem_session"


def _secret(data_dir: Path) -> bytes:
    p = data_dir / "session_secret"
    if not p.exists():
        p.write_bytes(secrets.token_bytes(32))
    return p.read_bytes()


def _token(secret: bytes, days: float) -> str:
    exp = str(int(time.time() + days * 86400))
    return f"{exp}.{hmac.new(secret, exp.encode(), hashlib.sha256).hexdigest()}"


def _valid(secret: bytes, token: str | None) -> bool:
    if not token or "." not in token:
        return False
    exp, sig = token.split(".", 1)
    good = hmac.new(secret, exp.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(sig, good) and exp.isdigit() and int(exp) > time.time()


def build_app(engine) -> FastAPI:
    cfg, db = engine.cfg, engine.db
    d = cfg.dashboard
    need_login = d.bind_host not in LOCAL
    password = cfg.secret("DASHBOARD_PASSWORD")
    secret = _secret(cfg.data_dir)
    fails: dict[str, list[float]] = {}
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def auth(request: Request, call_next):
        path = request.url.path
        if need_login and path.startswith("/api/") and path != "/api/login":
            if not _valid(secret, request.cookies.get(COOKIE)):
                return JSONResponse({"error": "login"}, status_code=401)
        resp = await call_next(request)
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Referrer-Policy"] = "no-referrer"
        return resp

    @app.get("/")
    async def index():
        return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})

    @app.post("/api/login")
    async def login(request: Request):
        ip = request.headers.get("x-forwarded-for", request.client.host if request.client else "?").split(",")[0]
        now = time.time()
        recent = [t for t in fails.get(ip, []) if now - t < 900]
        if len(recent) >= 8:
            raise HTTPException(429, "too many attempts - wait 15 minutes")
        try:
            given = str((await request.json()).get("password", ""))
        except (ValueError, AttributeError):
            given = ""
        if not (password and hmac.compare_digest(given.encode(), password.encode())):
            fails[ip] = recent + [now]
            raise HTTPException(401, "wrong password")
        resp = JSONResponse({"ok": True})
        resp.set_cookie(COOKIE, _token(secret, d.session_days), max_age=int(d.session_days * 86400), httponly=True,
                        samesite="strict", secure=bool(d.cookie_secure))
        return resp

    @app.get("/api/health")
    async def health():
        h = engine.health()
        h["login"] = need_login
        h["scope"] = dict(cfg.scope)
        h["plan"] = {"stop_pct": cfg.trade.stop_pct, "trail": dict(cfg.trade.trail), "max_days": cfg.trade.max_days,
                     "target_mult": cfg.label.target_mult, "stop_mult": cfg.label.stop_mult, "horizon_h": cfg.label.horizon_h}
        return h

    def call_row(r) -> dict:
        x = dict(r)
        x["reasons"] = json.loads(x.get("reasons") or "{}")
        x.pop("tg_msg", None)
        x.pop("state", None)
        p = engine.positions.get(x["id"])
        if p:
            x["peak_mult"], x["last_mult"], x["last_mcap"], x["peak_mcap"] = p.peak, p.last, p.last_mcap, p.peak_mcap
        x["result_mult"] = x["exit_mult"] if x["status"] == "closed" else x["last_mult"]
        return x

    @app.get("/api/calls")
    async def calls(limit: int = 200, status: str = ""):
        where, args = ("WHERE c.status=?", (status,)) if status in ("open", "closed") else ("", ())
        rows = db.query(f"SELECT c.*, t.chain, t.address, t.symbol, t.name, t.launchpad FROM calls c LEFT JOIN tokens t "
                        f"ON t.id=c.token_id {where} ORDER BY c.ts DESC LIMIT ?", args + (min(limit, 1000),))
        return [call_row(r) for r in rows]

    @app.get("/api/call")
    async def call(id: int):
        r = db.one("SELECT c.*, t.chain, t.address, t.symbol, t.name, t.launchpad, t.creator FROM calls c LEFT JOIN tokens t "
                   "ON t.id=c.token_id WHERE c.id=?", (id,))
        if not r:
            raise HTTPException(404, "unknown call")
        x = call_row(r)
        x["events"] = [dict(e) for e in db.query("SELECT ts, kind, label, price, mcap, mult, text FROM call_events "
                                                 "WHERE call_id=? ORDER BY ts, id", (id,))]
        s = db.one("SELECT fkeys, features, gate FROM snapshots WHERE id=?", (x["snapshot_id"],))
        x["factors"] = db.unpack(s["fkeys"], s["features"]) if s else {}
        x["bars"] = [[b["ts"], b["price"], b["mcap"]] for b in db.query(
            "SELECT ts, price, mcap FROM bars WHERE token_id=? AND ts>=? ORDER BY ts", (x["token_id"], x["ts"] - 3600))]
        return x

    @app.get("/api/candidates")
    async def candidates():
        """What is being watched right now, best first."""
        now, hot = time.time(), engine.open_tokens()
        out = []
        for t in engine.toks.values():
            if t.score is None or now - t.scope_ts > 3600:
                continue
            f = t.lists
            out.append({"id": t.id, "chain": t.chain, "address": t.address, "symbol": t.symbol, "name": t.name,
                        "launchpad": t.launchpad, "mcap": t.mcap, "liq": t.liq, "score": t.score, "gate": t.gate,
                        "good": t.good, "bad": t.bad, "groups": t.groups, "deep_ts": t.deep_ts or None, "open_call": t.id in hot,
                        "holders": f.get("h_holders"), "top10": f.get("h_top10_pct"), "smart": f.get("w_smart_n"),
                        "bundler": f.get("h_bundler_pct"), "insider": f.get("h_insider_pct"),
                        "vol_1h": (t.dex or {}).get("f_vol_1h"), "chg_1h": (t.dex or {}).get("p_chg_1h"),
                        "first_seen": t.first_seen, "snaps": t.n_snaps})
        out.sort(key=lambda x: (bool(x["gate"]), -(x["score"] or 0)))
        return out[:300]

    @app.get("/api/scoreboard")
    async def scoreboard(days: int = 30):
        since = time.time() - min(days, 365) * 86400
        closed = "status='closed'"
        total = db.one(
            f"SELECT COUNT(*) n, SUM(status='open') open, SUM({closed}) closed, SUM({closed} AND exit_mult>=1) wins, "
            f"AVG(CASE WHEN {closed} THEN exit_mult END) avg_exit, AVG(peak_mult) avg_peak, MAX(peak_mult) best_peak, "
            f"SUM(peak_mult>=2) x2, SUM(peak_mult>=3) x3, SUM(peak_mult>=5) x5, SUM(peak_mcap>=1000000) m1, "
            f"SUM(CASE WHEN {closed} THEN exit_mult-1 END) sum_r FROM calls WHERE ts>?", (since,))
        by_chain = db.query(
            f"SELECT t.chain, COUNT(*) n, SUM(c.{closed}) closed, SUM(c.{closed} AND c.exit_mult>=1) wins, "
            f"AVG(CASE WHEN c.{closed} THEN c.exit_mult END) avg_exit, AVG(c.peak_mult) avg_peak FROM calls c LEFT JOIN tokens t "
            f"ON t.id=c.token_id WHERE c.ts>? GROUP BY t.chain ORDER BY n DESC", (since,))
        by_day = db.query(
            f"SELECT date(ts,'unixepoch') day, COUNT(*) n, SUM({closed}) closed, SUM({closed} AND exit_mult>=1) wins, "
            f"AVG(CASE WHEN {closed} THEN exit_mult END) avg_exit, MAX(peak_mult) best_peak FROM calls WHERE ts>? "
            f"GROUP BY day ORDER BY day DESC", (since,))
        by_exit = db.query(f"SELECT exit_reason, COUNT(*) n, AVG(exit_mult) avg_exit FROM calls WHERE {closed} AND ts>? "
                           f"GROUP BY exit_reason ORDER BY n DESC", (since,))
        base = db.query("SELECT t.chain, s.stage, COUNT(*) n, AVG(s.hit) rate, AVG(s.peak_mult) avg_peak FROM snapshots s "
                        "LEFT JOIN tokens t ON t.id=s.token_id WHERE s.done=1 AND s.ts>? GROUP BY t.chain, s.stage "
                        "ORDER BY t.chain, s.stage", (since,))
        buckets = db.query("SELECT stage, CAST(score/10 AS INT)*10 bucket, COUNT(*) n, AVG(CASE WHEN done THEN hit END) rate, "
                           "AVG(fast_hit) fast_rate, AVG(peak_mult) avg_peak FROM snapshots WHERE (done=1 OR fast_hit IS NOT NULL) "
                           "AND ts>? GROUP BY stage, bucket ORDER BY stage, bucket", (since,))
        gated = db.query("SELECT (gate IS NOT NULL) gated, COUNT(*) n, AVG(hit) rate FROM snapshots WHERE done=1 AND ts>? "
                         "GROUP BY gated", (since,))
        return {"total": dict(total), "by_chain": [dict(r) for r in by_chain], "by_day": [dict(r) for r in by_day],
                "by_exit": [dict(r) for r in by_exit], "base_rates": [dict(r) for r in base],
                "score_buckets": [dict(r) for r in buckets], "gated": [dict(r) for r in gated]}

    @app.get("/api/learning")
    async def learning():
        models = []
        for r in db.query("SELECT ver, ts, n_rows, n_pos, active, metrics FROM models ORDER BY id DESC LIMIT 20"):
            x = dict(r)
            m = json.loads(x.pop("metrics") or "{}")
            x["test"], x["threshold"], x["importance"] = m.get("test"), m.get("threshold"), m.get("importance")
            models.append(x)
        graded = db.one("SELECT SUM(done) n, SUM(done AND hit) hit, SUM(fast_hit IS NOT NULL) fast_n, SUM(fast_hit) fast_hit "
                        "FROM snapshots")
        w = db.one("SELECT COUNT(*) n, SUM(n_tok>=?) seasoned FROM wallets", (cfg.learn.wallets.min_tokens,))
        return {"models": models, "lift": db.get("lift") or [], "last": engine.last_train,
                "graded": dict(graded), "need": {"rows": cfg.learn.min_rows, "pos": cfg.learn.min_pos},
                "auto_retrain": bool(cfg.learn.auto_retrain),
                "tuning": engine.tuning, "tuning_last": db.get("tuning_last"), "tune_cfg": dict(cfg.learn.tune),
                "fast": dict(cfg.label.fast), "weights": dict(cfg.score.weights),
                "wallets": {"tracked": w["n"], "seasoned": w["seasoned"] or 0, "base": engine.history.base,
                            "proven": engine.history.top(25)},
                "deployers": [dict(r) for r in db.query(
                    "SELECT addr, n_tok, n_win, n_dead, n_called, n_rug, best_mult FROM deployers WHERE n_tok>=2 OR n_called>0 "
                    "ORDER BY n_rug DESC, n_win DESC, n_tok DESC LIMIT 30")]}

    @app.post("/api/retrain")
    async def retrain(request: Request):
        """Start a retrain in the background; GET /api/retrain has the result. {"promote": false} only reports
        how a new model compares with what is in use; {"promote": true} switches to it if it passes the tests."""
        try:
            body = await request.json()
        except ValueError:
            body = {}
        if engine.retraining:
            raise HTTPException(409, "a retrain is already running")
        promote = bool((body or {}).get("promote"))
        engine.retraining = {"started": time.time(), "promote": promote}

        async def job():
            try:
                await engine.retrain(promote=promote)
            finally:
                engine.retraining = False
        engine.spawn(job())
        return {"started": True, "promote": promote}

    @app.get("/api/retrain")
    async def retrain_status():
        active = db.one("SELECT ver, ts FROM models WHERE active=1 ORDER BY id DESC LIMIT 1")
        return {"running": engine.retraining or None, "last": {k: v for k, v in engine.last_train.items() if k != "importance"},
                "in_use": dict(active) if active else None, "auto_retrain": bool(cfg.learn.auto_retrain)}

    @app.get("/api/research")
    async def research_list():
        """Days of research data on this server (tools/sync_research.py copies them to the PC)."""
        db.commit()
        return {"days": await asyncio.to_thread(list_days, db.path), "server_time": time.time()}

    @app.get("/api/research/file/{name}")
    async def research_file(name: str):
        m = NAME.fullmatch(name)
        if not m:
            raise HTTPException(404, "no such file")
        db.commit()
        data = await asyncio.to_thread(build_day, db.path, m.group(1))
        return Response(data, media_type="application/gzip",
                        headers={"Content-Disposition": f'attachment; filename="{name}"', "Cache-Control": "no-store"})

    @app.get("/api/token")
    async def token(id: str):
        t = db.one("SELECT * FROM tokens WHERE id=?", (id,))
        if not t:
            raise HTTPException(404, "unknown token")
        snaps = []
        for s in db.query("SELECT * FROM snapshots WHERE token_id=? ORDER BY ts DESC LIMIT 12", (id,)):
            y = dict(s)
            y["features"] = db.unpack(y.pop("fkeys"), y["features"])
            snaps.append(y)
        live = engine.toks.get(id)
        return {"token": dict(t), "snapshots": snaps,
                "live": {"score": live.score, "gate": live.gate, "good": live.good, "bad": live.bad, "groups": live.groups}
                if live else None}

    return app


async def serve(engine) -> None:
    d = engine.cfg.dashboard
    if d.bind_host not in LOCAL and not engine.cfg.secret("DASHBOARD_PASSWORD"):
        log.error("dashboard NOT started: it would be reachable from the network and DASHBOARD_PASSWORD is not set")
        return
    config = uvicorn.Config(build_app(engine), host=d.bind_host, port=d.port, log_level="warning", lifespan="off",
                            proxy_headers=True, forwarded_allow_ips="*")
    log.info("dashboard on %s", d.base_url)
    await uvicorn.Server(config).serve()
