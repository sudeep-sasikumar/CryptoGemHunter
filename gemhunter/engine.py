"""The loop: find tokens in the market-cap range on every chain, measure them, snapshot every one, look deeper at
the best, call the few that pass, run each call's trade plan, grade everything, and learn from the grades.

    GMGN rank + trenches (per chain) ─► watched token ─► factors ─► gates + scorecard ─► SNAPSHOT (every token)
                                                              │
                                              best candidates ▼
                         deep look: token page, security, holders, traders, candles, deployer history, GoPlus
                                                              │ gates (final) + scorecard / model
                                                              ▼
                                              CALL ─► Telegram entry ─► trade plan on live prices
                                                        (updates, warnings, exit)       │
    every snapshot is followed to its outcome (3x before -50%, 7 days) ◄────────────────┘
    daily: retrain on all outcomes, test on the newest unseen data, promote only if better

State lives in the database: a restart reloads open calls and unfinished snapshots and carries on.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from collections import deque

from . import factors as F
from . import narrative as N
from .db import DB
from .http import Http
from .model import Model, train
from .notify import Telegram, entry_text, esc, event_text, message_ids, money
from .scoring import gates, scorecard
from .sources.dexscreener import DexScreener
from .sources.extra import Gecko, GoPlus, Market
from .sources.gmgn import Gmgn
from .sources.social import Social
from .trade import Event, Follow, Position
from .tuning import tune
from .wallets import History

log = logging.getLogger("engine")
RUG_EXITS = ("liquidity pulled", "dev sold out", "rug risk", "honeypot", "wash trading")


class Tok:
    __slots__ = ("id", "chain", "address", "symbol", "name", "launchpad", "creator", "lists", "lists_ts", "dex", "dex_ts",
                 "deep", "deep_ts", "price", "mcap", "liq", "price_ts", "poll_ts", "obs", "first_seen", "scope_ts",
                 "snap_ts", "n_snaps", "follows", "early", "score", "gate", "good", "bad", "eval_ts", "bar_ts", "peak_mcap",
                 "saved_ts", "groups", "tg", "web")

    def __init__(self, chain: str, address: str, now: float):
        self.id, self.chain, self.address = f"{chain}:{address}", chain, address
        self.symbol = self.name = self.launchpad = self.creator = ""
        self.lists: dict = {}
        self.dex: dict = {}
        self.deep: dict = {}
        self.lists_ts = self.dex_ts = self.deep_ts = self.price_ts = self.poll_ts = 0.0
        self.price = self.mcap = self.liq = None
        self.obs: deque = deque(maxlen=400)
        self.first_seen, self.scope_ts = now, 0.0
        self.snap_ts, self.n_snaps = 0.0, 0
        self.follows: list[Follow] = []
        self.early: list = []
        self.score, self.gate, self.good, self.bad, self.groups = None, None, [], [], {}
        self.eval_ts = self.bar_ts = self.saved_ts = 0.0
        self.peak_mcap = None
        self.tg = self.web = None


class Engine:
    def __init__(self, cfg):
        self.cfg = cfg
        self.db = DB(cfg.data_dir / "gems.db")
        self.http = Http()
        g = cfg.sources.gmgn
        self.gmgn = Gmgn(g, cfg.secret("GMGN_API_KEY"))
        self.dex = DexScreener(self.http, cfg.sources.dexscreener)
        self.goplus = GoPlus(self.http, cfg.sources.goplus)
        self.market = Market(self.http)
        self.gecko = Gecko(self.http, cfg.sources.gecko)
        self.social = Social(self.http, cfg.sources.social)
        self.meta = N.Meta()
        self.context: dict[str, dict] = {}                # per chain: where money is flowing
        self.pad_rate: dict[str, float] = {}
        self.context_ts = 0.0
        self.telegram = Telegram(cfg.telegram, cfg.secret("TELEGRAM_BOT_TOKEN"), cfg.secret("TELEGRAM_CHAT_ID"), self.db)
        self.history = History(self.db, cfg.learn.wallets)
        self.toks: dict[str, Tok] = {}
        self.positions: dict[int, Position] = {}
        self.pos_safety: dict[int, float] = {}
        self.scores: deque = deque(maxlen=60000)           # (ts, score) of every snapshot: the call threshold's yardstick
        self.deep_times: deque = deque(maxlen=500)
        self.model: Model | None = None
        self.last_train: dict = self.db.get("last_train", {}) or {}
        self.tuning: dict = self.db.get("tuning", {}) or {}        # scorecard multipliers learned so far
        self.retraining: dict | bool = False
        self.started = time.time()
        self.counts = {"rank": 0, "trench": 0, "signals": 0, "trades": 0, "snapshots": 0, "deep": 0, "calls": 0, "labels": 0}
        self.sweep_ts: dict[str, float] = {}
        self.errors: deque = deque(maxlen=20)
        self._tasks: list[asyncio.Task] = []
        self._bg: set[asyncio.Task] = set()
        self._sweep_i = 0
        self._load()

    # ------------------------------------------------------------------ start-up: carry on where it stopped
    def _load(self) -> None:
        now = time.time()
        m = self.db.one("SELECT path FROM models WHERE active=1 ORDER BY id DESC LIMIT 1")
        if m and m["path"] and os.path.exists(m["path"]):
            self.model = Model.load(m["path"])
        for r in self.db.query("SELECT ts, score FROM snapshots WHERE ts>? AND score IS NOT NULL ORDER BY ts", (now - 7 * 86400,)):
            self.scores.append((r["ts"], r["score"]))
        ids = {r["token_id"] for r in self.db.query("SELECT DISTINCT token_id FROM snapshots WHERE done=0")}
        ids |= {r["token_id"] for r in self.db.query("SELECT token_id FROM calls WHERE status='open'")}
        ids |= {r["id"] for r in self.db.query("SELECT id FROM tokens WHERE last_seen>?", (now - 3600,))}
        for tid in ids:
            t = self.db.one("SELECT * FROM tokens WHERE id=?", (tid,))
            if not t:
                continue
            tok = Tok(t["chain"], t["address"], t["first_seen"] or now)
            tok.symbol, tok.name, tok.launchpad, tok.creator = t["symbol"] or "", t["name"] or "", t["launchpad"] or "", t["creator"] or ""
            tok.price, tok.mcap, tok.liq, tok.peak_mcap = t["last_price"], t["last_mcap"], t["last_liq"], t["peak_mcap"]
            n = self.db.one("SELECT COUNT(*) n, MAX(ts) ts FROM snapshots WHERE token_id=? AND stage='scan'", (tid,))
            tok.n_snaps, tok.snap_ts = n["n"], n["ts"] or 0.0
            tok.early = [(r["addr"], r["first_ts"], r["buy_usd"]) for r in
                         self.db.query("SELECT addr, first_ts, buy_usd FROM token_wallets WHERE token_id=?", (tid,))]
            self.toks[tid] = tok
        for r in self.db.query("SELECT * FROM snapshots WHERE done=0"):
            tok = self.toks.get(r["token_id"])
            if tok and r["entry_price"]:
                fl = Follow(r["id"], r["entry_price"], r["ts"], r["mcap"], r["peak_mult"] or 1.0, r["min_mult"] or 1.0,
                            r["end_mult"] or 1.0, bool(r["hit"]), bool(r["stopped"]), r["t_hit"], r["t_stop"], r["t_peak"])
                fl.fast, fl.t_fast = r["fast_hit"], r["t_fast"]
                tok.follows.append(fl)
        for r in self.db.query("SELECT * FROM calls WHERE status='open'"):
            p = Position(r["id"], r["token_id"], r["ts"], r["entry_price"], r["entry_mcap"], r["entry_liq"],
                         r["peak_mult"] or 1.0, r["peak_mcap"], r["last_mult"] or 1.0, r["last_mcap"], r["last_ts"] or r["ts"])
            p.load_state(json.loads(r["state"] or "{}"))
            self.positions[p.call_id] = p
        if self.toks:
            log.info("resumed: %d tokens, %d unfinished snapshots, %d open calls", len(self.toks),
                     sum(len(t.follows) for t in self.toks.values()), len(self.positions))

    # ------------------------------------------------------------------ helpers
    def spawn(self, coro) -> None:
        t = asyncio.ensure_future(coro)
        self._bg.add(t)
        t.add_done_callback(self._bg.discard)

    def in_scope(self, mcap: float | None) -> bool:
        s = self.cfg.scope
        return mcap is not None and s.mcap_min_usd <= mcap <= s.mcap_max_usd

    def open_tokens(self) -> set[str]:
        return {p.token_id for p in self.positions.values()}

    def touch(self, chain: str, address: str, f: dict, now: float) -> Tok | None:
        """A token seen in a GMGN list: start watching it (when in range) and take in the list's figures."""
        tid = f"{chain}:{address}"
        tok = self.toks.get(tid)
        mcap = f.get("_mcap")
        if tok is None:
            if not self.in_scope(mcap) or len(self.toks) >= self.cfg.watch.max_tokens:
                return None
            tok = self.toks[tid] = Tok(chain, address, now)
            self.db.execute("INSERT OR IGNORE INTO tokens(id, chain, address, first_seen, first_mcap) VALUES(?,?,?,?,?)",
                            (tid, chain, address, now, mcap))
        F.put(tok.lists, f)
        tok.lists_ts = now
        tok.launchpad = (f.get("_launchpad") or tok.launchpad or "").lower()
        tok.creator = f.get("_creator") or tok.creator
        if self.in_scope(mcap):
            tok.scope_ts = now
        tok.tg, tok.web = f.get("_tg") or tok.tg, f.get("_web") or tok.web
        last = tok.obs[-1] if tok.obs else (0,) * 8        # the two lists carry different fields: keep the last known
        tok.obs.append((now, f.get("h_holders") or last[1], f.get("h_top10_pct") if f.get("h_top10_pct") is not None else last[2],
                        mcap, f.get("a_tg_calls") if f.get("a_tg_calls") is not None else last[4],
                        f.get("a_callouts") if f.get("a_callouts") is not None else last[5],
                        f.get("a_visits") if f.get("a_visits") is not None else last[6],
                        f.get("a_x_followers") if f.get("a_x_followers") is not None else last[7]))
        if now - tok.dex_ts > 300 and f.get("_price") and self.price_ok(tok, f["_price"], mcap):
            self.on_price(tok, f["_price"], mcap, f.get("l_liq_usd"), now)   # DexScreener has not priced it: GMGN's does
        return tok

    def factors(self, tok: Tok, now: float) -> dict:
        f: dict = {"chain": tok.chain, "launchpad": tok.launchpad or "none"}
        F.put(f, tok.lists)
        if tok.deep and now - tok.deep_ts < 6 * 3600:
            F.put(f, tok.deep)
        F.put(f, tok.dex if now - tok.dex_ts < 900 else None)
        if tok.price and now - tok.price_ts < 900:
            f["_price"] = tok.price
        sig = self.db.query("SELECT type, trigger_at FROM gm_signals WHERE address=? AND trigger_at>?", (tok.address, now - 86400))
        F.put(f, F.from_signals([(r["type"], r["trigger_at"]) for r in sig], now))
        tr = self.db.query("SELECT kind, maker, side, usd, price, closing, ts FROM sm_trades WHERE address=? AND ts>?",
                           (tok.address, now - 6 * 3600))
        F.put(f, F.from_feed([tuple(r) for r in tr], now, f.get("_price")))
        F.put(f, F.from_history(list(tok.obs)))
        F.put(f, self.market.factors(tok.chain))
        F.put(f, self.history.factors(tok.id, tok.creator, tok.early))
        # narrative, meme shape, original-or-copy, where money is rotating: measured from the watched market itself
        ws = N.words(tok.symbol, tok.name)
        f["narrative"] = N.theme(ws | N.words(f.get("_desc")), f.get("_cg_categories"))
        F.put(f, N.shape(tok.symbol, tok.name, f.get("_desc") or "", f.get("_logo")))
        F.put(f, self.meta.factors(tok.id, ws))
        if tok.symbol:
            twins = self.db.one("SELECT COUNT(*) n, MIN(created_ts) oldest FROM tokens WHERE symbol=? COLLATE NOCASE AND id!=?",
                                (tok.symbol, tok.id))
            f["a_symbol_twins_n"] = twins["n"]
            if twins["n"] and f.get("_created"):
                f["a_symbol_oldest"] = int(twins["oldest"] is None or f["_created"] <= twins["oldest"])
        F.put(f, self.context.get(tok.chain))
        F.put(f, {"m_pad_fast_rate_7d": self.pad_rate.get(tok.launchpad or "none")})
        return F.derive(f, now)

    def judge(self, f: dict, final: bool = False):
        bad_gates = gates(f, self.cfg.gates, self.cfg.scope, final=final)
        score, groups, good, bad = scorecard(f, self.cfg.score.weights, self.tuning)
        prob = None
        if self.model is not None:
            try:
                prob = self.model.predict(F.stored(f))
            except Exception:   # noqa: BLE001 - a model problem must never stop the scanner
                log.exception("model prediction failed")
        return bad_gates, score, groups, good, bad, prob

    # ------------------------------------------------------------------ prices, labels, trade plan
    def on_price(self, tok: Tok, price: float | None, mcap: float | None, liq: float | None, now: float) -> None:
        if price:
            tok.price, tok.price_ts = price, now
        if mcap:
            tok.mcap = mcap
            tok.peak_mcap = max(tok.peak_mcap or 0, mcap)
            if self.in_scope(mcap):
                tok.scope_ts = now
        if liq is not None:
            tok.liq = liq
        for fl in tok.follows:
            if fl.tick(price, liq, now, self.cfg.label):
                self.finish_follow(tok, fl, now)
        tok.follows = [fl for fl in tok.follows if not fl.done]
        for p in [p for p in self.positions.values() if p.token_id == tok.id]:
            ev = p.lost() if price is None and liq == 0.0 else p.tick(price, mcap, liq, now, self.cfg.trade)
            if ev:
                self.spawn(self.handle_events(p, tok, ev, now))
        if (tok.deep_ts or tok.id in self.open_tokens()) and price and now - tok.bar_ts >= 600:
            tok.bar_ts = now
            self.db.execute("INSERT OR REPLACE INTO bars(token_id, ts, price, mcap, liq) VALUES(?,?,?,?,?)",
                            (tok.id, round(now), price, mcap, liq))

    def finish_follow(self, tok: Tok, fl: Follow, now: float) -> None:
        self.counts["labels"] += 1
        self.db.execute(
            "UPDATE snapshots SET done=1, hit=?, stopped=?, peak_mult=?, min_mult=?, end_mult=?, peak_mcap=?, t_hit=?, t_stop=?, "
            "t_peak=?, end_reason=?, done_ts=?, last_ts=?, fast_hit=?, t_fast=? WHERE id=?",
            (int(fl.hit), int(fl.stopped), fl.peak, fl.low, fl.last, (fl.entry_mcap or 0) * fl.peak or None, fl.t_hit, fl.t_stop,
             fl.t_peak, fl.reason, now, now, fl.fast, fl.t_fast, fl.snap_id))

    def flush(self, now: float) -> None:
        """Write what changed since the last flush: running labels, open calls, token rows."""
        rows = []
        for tok in self.toks.values():
            for fl in tok.follows:
                if fl.dirty:
                    fl.dirty = False
                    rows.append((int(fl.hit), int(fl.stopped), fl.peak, fl.low, fl.last, fl.t_hit, fl.t_stop, fl.t_peak, now,
                                 fl.fast, fl.t_fast, fl.snap_id))
            if tok.price_ts > tok.saved_ts or tok.lists_ts > tok.saved_ts:
                tok.saved_ts = now
                self.db.execute(
                    "UPDATE tokens SET symbol=?, name=?, launchpad=?, creator=?, last_seen=?, peak_mcap=?, last_mcap=?, last_price=?, "
                    "last_liq=?, created_ts=COALESCE(?, created_ts), open_ts=COALESCE(?, open_ts), updated_ts=? WHERE id=?",
                    (tok.symbol, tok.name, tok.launchpad, tok.creator, max(tok.scope_ts, tok.price_ts), tok.peak_mcap, tok.mcap,
                     tok.price, tok.liq, tok.lists.get("_created"), tok.lists.get("_opened"), now, tok.id))
        if rows:
            self.db.executemany("UPDATE snapshots SET hit=?, stopped=?, peak_mult=?, min_mult=?, end_mult=?, t_hit=?, t_stop=?, "
                                "t_peak=?, last_ts=?, fast_hit=?, t_fast=? WHERE id=?", rows)
        for p in self.positions.values():
            self.db.execute("UPDATE calls SET peak_mult=?, peak_mcap=?, last_mult=?, last_mcap=?, last_ts=?, state=? WHERE id=?",
                            (p.peak, p.peak_mcap, p.last, p.last_mcap, p.last_ts, json.dumps(p.state()), p.call_id))
        self.db.commit()

    async def handle_events(self, p: Position, tok: Tok, events: list[Event], now: float) -> None:
        call = self.db.one("SELECT tg_msg FROM calls WHERE id=?", (p.call_id,))
        reply = message_ids(call["tg_msg"]) if call else {}
        slip = self.cfg.trade.entry_slippage_pct / 100
        for ev in events:
            if ev.kind == "exit":
                ev.mult *= 1 - slip                        # the sell fills below the last price too
                ev.price = (ev.price or tok.price or 0) * (1 - slip) or None
                rug = any(ev.label.startswith(x) for x in RUG_EXITS)
                self.db.execute("UPDATE calls SET status='closed', exit_ts=?, exit_price=?, exit_mult=?, exit_reason=?, peak_mult=?, "
                                "peak_mcap=?, last_mult=?, last_ts=?, state=? WHERE id=?",
                                (now, ev.price, ev.mult, ev.label, p.peak, p.peak_mcap, p.last, now, json.dumps(p.state()), p.call_id))
                self.history.call_closed(tok.creator, rug, now)
                self.positions.pop(p.call_id, None)
            text = event_text(tok.symbol or tok.address[:6], ev, p.entry, now - p.ts, p.peak)
            self.db.execute("INSERT INTO call_events(call_id, ts, kind, label, price, mcap, mult, text) VALUES(?,?,?,?,?,?,?,?)",
                            (p.call_id, now, ev.kind, ev.label, ev.price or tok.price, ev.mcap or tok.mcap, ev.mult, ev.detail))
            self.db.commit()
            log.info("call %d %s %s: %s (%.2fx)", p.call_id, tok.symbol, ev.kind, ev.label, ev.mult)
            await self.telegram.send(text, dedupe=f"call{p.call_id}:{ev.kind}:{ev.label}", kind=ev.kind, reply_to=reply,
                                     silent=ev.kind == "update" and bool(self.cfg.telegram.silent_updates))
        self.db.commit()

    # ------------------------------------------------------------------ snapshots, deep looks, calls
    @staticmethod
    def price_ok(tok: Tok, price: float | None, mcap: float | None) -> bool:
        """A price agrees with the market cap the same source gives (price x supply) and with the last price seen.
        Guards the grades against a quote in the wrong unit."""
        if not price or price <= 0:
            return False
        supply = tok.lists.get("_supply") or tok.deep.get("_supply")
        if supply and mcap and not (1 / 20 <= price * supply / mcap <= 20):
            return False
        return not (tok.price and tok.price_ts and not (1 / 50 <= price / tok.price <= 50))

    def snapshot(self, tok: Tok, f: dict, stage: str, score: float, prob: float | None, gate: list[str], now: float) -> int | None:
        price = f.get("_price")
        if not price or (f.get("l_liq_usd") is not None and f["l_liq_usd"] < 100) or not self.price_ok(tok, price, f.get("_mcap")):
            return None
        entry = price * (1 + self.cfg.label.slippage_pct / 100)
        kid, vals = self.db.pack(F.stored({**f, "stage": stage}))
        sid = self.db.execute(
            "INSERT INTO snapshots(token_id, ts, stage, price, entry_price, mcap, liq, fkeys, features, score, prob, model_ver, gate) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (tok.id, now, stage, price, entry, f.get("_mcap"), f.get("l_liq_usd"), kid, vals, score, prob,
             self.model.ver if self.model and prob is not None else None, "; ".join(gate)[:300] or None)).lastrowid
        tok.follows.append(Follow(sid, entry, now, f.get("_mcap")))
        self.scores.append((now, score))
        self.counts["snapshots"] += 1
        return sid

    def evaluate(self, tok: Tok, now: float) -> None:
        f = self.factors(tok, now)
        bad_gates, score, groups, good, bad, prob = self.judge(f)
        tok.score, tok.gate, tok.good, tok.bad, tok.groups, tok.eval_ts = score, bad_gates, good, bad, groups, now
        s = self.cfg.snapshot
        if self.in_scope(f.get("_mcap")) and tok.n_snaps < s.max_per_token and now - tok.snap_ts >= s.every_h * 3600:
            if self.snapshot(tok, f, "scan", score, prob, bad_gates, now):
                tok.snap_ts, tok.n_snaps = now, tok.n_snaps + 1

    async def deep_look(self, tok: Tok) -> None:
        """Everything GMGN and GoPlus know about one token, then the final judgement and, maybe, a call."""
        d, now = self.cfg.deep, time.time()
        tok.deep_ts = now                                  # set first: no second look while this one runs
        self.deep_times.append(now)
        self.counts["deep"] += 1
        deep: dict = {}
        info = await self.gmgn.info(tok.chain, tok.address)
        if info:
            F.put(deep, F.from_info(info, tok.address))
            tok.symbol, tok.name = str(info.get("symbol") or tok.symbol)[:40], str(info.get("name") or tok.name)[:80]
            tok.creator = deep.get("_creator") or tok.creator
        sec = await self.gmgn.security(tok.chain, tok.address)
        if sec:
            F.put(deep, F.from_security(sec))
        if tok.chain != "sol":
            F.put(deep, await self.goplus.check(tok.chain, tok.address))
        try:                                               # free public sources: no GMGN budget is spent here
            F.put(deep, await self.social.look(tok.chain, tok.address, deep.get("_tg") or tok.tg, deep.get("_web") or tok.web))
        except Exception:   # noqa: BLE001 - creator-supplied links must never stop a deep look
            log.exception("social look %s failed", tok.id)
        price = deep.get("_price") or tok.price
        candles = await self.gecko.candles(tok.chain, (tok.dex or {}).get("_pool"), int(d.kline.days * 24))    # free
        if not candles:
            candles = await self.gmgn.kline(tok.chain, tok.address, d.kline.resolution, d.kline.days)
        if candles:
            F.put(deep, F.from_kline(candles, price, deep.get("_created") or tok.lists.get("_created"), d.kline.days))
        if d.created_tokens and tok.creator:
            made = await self.gmgn.created_tokens(tok.chain, tok.creator)
            if made:
                F.put(deep, F.from_created(made, tok.address))
        holders = await self.gmgn.holders(tok.chain, tok.address) if d.holders else None
        if holders:
            F.put(deep, F.from_holders(holders, price))
        traders = await self.gmgn.traders(tok.chain, tok.address) if d.traders else None
        if traders:
            F.put(deep, F.from_traders(traders, deep.get("_mcap") or tok.mcap))
        buyers = F.early_buyers(holders, traders, int(self.cfg.learn.wallets.early_n))
        if buyers:
            tok.early = buyers
            self.history.record_early(tok.id, buyers)
        deep["deep_holders"], deep["deep_traders"] = int(bool(holders)), int(bool(traders))
        deep["deep_page"], deep["deep_candles"] = int(bool(info)), int(bool(candles))
        tok.deep, now = deep, time.time()
        f = self.factors(tok, now)
        bad_gates, score, groups, good, bad, prob = self.judge(f, final=True)
        tok.score, tok.gate, tok.good, tok.bad, tok.groups, tok.eval_ts = score, bad_gates, good, bad, groups, now
        sid = self.snapshot(tok, f, "deep", score, prob, bad_gates, now)
        self.db.commit()
        if sid and not bad_gates:
            why = self.call_block(tok, score, prob, now)
            if why is None:
                await self.open_call(tok, f, sid, score, prob, good, bad, now)
            else:
                log.info("deep look %s %s: score %.0f, no call (%s)", tok.chain, tok.symbol, score, why)
        elif bad_gates:
            log.info("deep look %s %s: score %.0f, gated (%s)", tok.chain, tok.symbol, score, "; ".join(bad_gates))

    def call_threshold(self, now: float) -> float | None:
        """Scorecard a call needs: the configured minimum, and the best `top_pct` of every snapshot of the last
        7 days. None while too few snapshots exist to rank against."""
        c = self.cfg.call
        recent = sorted(s for ts, s in self.scores if now - ts < 7 * 86400)
        if len(recent) < int(c.get("warmup_snapshots") or 0):
            return None
        if not recent:
            return float(c.min_score)
        return max(float(c.min_score), recent[min(len(recent) - 1, int(len(recent) * (1 - c.top_pct / 100)))])

    def call_block(self, tok: Tok, score: float, prob: float | None, now: float) -> str | None:
        """Why this passing token is not called (None = call it)."""
        c = self.cfg.call
        if tok.id in self.open_tokens():
            return "already an open call"
        if self.db.one("SELECT 1 FROM calls WHERE token_id=? AND ts>?", (tok.id, now - c.token_cooldown_h * 3600)):
            return "called within the cooldown"
        if len(self.positions) >= c.max_open:
            return "too many open calls"
        if self.db.one("SELECT COUNT(*) n FROM calls WHERE ts>?", (now - 86400,))["n"] >= c.max_per_day:
            return "daily call cap reached"
        if self.db.one("SELECT COUNT(*) n FROM calls WHERE ts>?", (now - 3600,))["n"] >= int(c.get("max_per_hour") or 99):
            return "hourly call cap reached"
        if self.model is not None and prob is not None:
            thr = max(self.model.thr, float(c.min_prob))
            return None if prob >= thr else f"chance {prob * 100:.0f}% under {thr * 100:.0f}%"
        thr = self.call_threshold(now)
        if thr is None:
            return f"warming up ({len(self.scores)}/{c.warmup_snapshots} snapshots)"
        return None if score >= thr else f"score under {thr:.0f}"

    async def open_call(self, tok: Tok, f: dict, sid: int, score: float, prob: float | None, good, bad, now: float) -> None:
        price = f["_price"]
        entry = price * (1 + self.cfg.trade.entry_slippage_pct / 100)
        ver = self.model.ver if self.model and prob is not None else None
        cid = self.db.execute(
            "INSERT INTO calls(snapshot_id, token_id, ts, entry_price, entry_mcap, entry_liq, score, prob, model_ver, reasons, "
            "last_ts) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (sid, tok.id, now, entry, f.get("_mcap"), f.get("l_liq_usd"), score, prob, ver,
             json.dumps({"good": good, "bad": bad, "groups": tok.groups}), now)).lastrowid
        self.db.execute("UPDATE snapshots SET called=1 WHERE id=?", (sid,))
        p = Position(cid, tok.id, now, entry, f.get("_mcap"), f.get("l_liq_usd"), last_ts=now)
        p.dev_closed_at_entry = f.get("d_closed")
        self.positions[cid] = p
        self.pos_safety[cid] = now
        self.counts["calls"] += 1
        text = entry_text(tok, f, score, prob, ver, good, bad, entry, self.cfg.trade, self.cfg.label)
        self.db.execute("INSERT INTO call_events(call_id, ts, kind, label, price, mcap, mult, text) VALUES(?,?,?,?,?,?,?,?)",
                        (cid, now, "entry", "entry", entry, f.get("_mcap"), 1.0, ""))
        self.db.commit()
        log.info("CALL %d: %s %s at mcap %s, score %.0f", cid, tok.chain, tok.symbol, money(f.get("_mcap")), score)
        ids = await self.telegram.send(text, dedupe=f"call{cid}:entry", kind="entry")
        if ids:
            self.db.execute("UPDATE calls SET tg_msg=? WHERE id=?", (json.dumps(ids), cid))
        self.db.commit()

    # ------------------------------------------------------------------ loops
    async def loop_rank(self) -> None:
        r = self.cfg.sources.gmgn.rank
        while True:
            sweep = r.sweeps[self._sweep_i % len(r.sweeps)]
            self._sweep_i += 1
            for chain in self.cfg.chains:
                flt = list(r.filters.get("sol" if chain == "sol" else "evm") or [])
                items = await self.gmgn.rank(chain, sweep["interval"], sweep["order_by"], r.limit, flt,
                                             self.cfg.scope.mcap_min_usd, self.cfg.scope.mcap_max_usd)
                now = time.time()
                for i in items:
                    tok = self.touch(chain, str(i["address"]), F.from_rank(i, sweep["interval"]), now)
                    if tok:
                        tok.symbol, tok.name = str(i.get("symbol") or tok.symbol)[:40], str(i.get("name") or tok.name)[:80]
                self.counts["rank"] += len(items)
                if items:
                    self.sweep_ts[chain] = now
            await asyncio.sleep(r.every_s)

    async def loop_trenches(self) -> None:
        t = self.cfg.sources.gmgn.trenches
        await asyncio.sleep(45)
        while True:
            for chain in self.cfg.chains:
                items = await self.gmgn.trenches(chain, t.limit, self.cfg.scope.mcap_min_usd, self.cfg.scope.mcap_max_usd)
                now = time.time()
                for i in items:
                    tok = self.touch(chain, str(i["address"]), F.from_trench(i), now)
                    if tok:
                        tok.symbol, tok.name = str(i.get("symbol") or tok.symbol)[:40], str(i.get("name") or tok.name)[:80]
                self.counts["trench"] += len(items)
            await asyncio.sleep(t.every_s)

    async def loop_signals(self) -> None:
        s = self.cfg.sources.gmgn.signals
        await asyncio.sleep(20)
        while True:
            for chain in self.cfg.chains:
                items = await self.gmgn.signals(chain, list(s.types), self.cfg.scope.mcap_min_usd, self.cfg.scope.mcap_max_usd)
                now = time.time()
                rows = [(str(i["id"])[:64], chain, str(i["token_address"])[:80], int(F.num(i.get("signal_type")) or 0),
                         F.num(i.get("trigger_at")), F.num(i.get("trigger_mc")), F.num(i.get("first_trigger_mc")),
                         F.num(i.get("signal_times")), now) for i in items]
                if rows:
                    self.db.executemany("INSERT OR IGNORE INTO gm_signals(id, chain, address, type, trigger_at, trigger_mc, "
                                        "first_mc, times, seen_ts) VALUES(?,?,?,?,?,?,?,?,?)", rows)
                    self.counts["signals"] += len(rows)
            self.db.commit()
            await asyncio.sleep(s.every_s)

    async def loop_feed(self) -> None:
        s = self.cfg.sources.gmgn.smart
        await asyncio.sleep(30)
        while True:
            for kind in ("smart", "kol"):
                for chain in s.chains:
                    if chain not in self.cfg.chains:
                        continue
                    items = await self.gmgn.feed(kind, chain, s.limit)
                    rows = [(f"{kind}:{i.get('transaction_hash')}:{i.get('maker')}:{i.get('side')}"[:200], chain, kind,
                             str(i["maker"])[:80], str(i["base_address"])[:80], str(i.get("side") or "")[:4],
                             F.num(i.get("amount_usd")), F.num(i.get("price_usd")), int(F.num(i.get("is_open_or_close")) or 0),
                             F.num(i.get("timestamp"))) for i in items]
                    if rows:
                        self.db.executemany("INSERT OR IGNORE INTO sm_trades(id, chain, kind, maker, address, side, usd, price, "
                                            "closing, ts) VALUES(?,?,?,?,?,?,?,?,?,?)", rows)
                        self.counts["trades"] += len(rows)
            self.db.commit()
            await asyncio.sleep(s.every_s)

    async def loop_prices(self) -> None:
        w = self.cfg.watch
        while True:
            now, hot = time.time(), self.open_tokens()
            due: dict[str, list[Tok]] = {}
            for tok in self.toks.values():
                every = w.call_price_every_s if tok.id in hot else w.price_every_s
                if now - tok.poll_ts >= every and (tok.follows or tok.id in hot or now - tok.scope_ts < w.drop_after_h * 3600):
                    tok.poll_ts = now
                    due.setdefault(tok.chain, []).append(tok)
            for chain, toks in due.items():
                toks.sort(key=lambda t: t.id not in hot)
                pairs = await self.dex.pairs(chain, [t.address for t in toks])
                now = time.time()
                for tok in toks:
                    d = pairs.get(tok.address)
                    if d and d.get("price"):
                        tok.dex, tok.dex_ts = F.from_dex(d), now
                        self.on_price(tok, d["price"], d.get("mcap"), d.get("liq"), now)
                    elif tok.dex_ts and now - tok.dex_ts > 1800 and now - tok.lists_ts > 1800:
                        self.on_price(tok, None, None, 0.0, now)       # DexScreener knew it and lost it: the pool is gone
            await asyncio.sleep(5)

    async def loop_evaluate(self) -> None:
        d = self.cfg.deep
        await asyncio.sleep(60)
        while True:
            now, n = time.time(), 0
            self.meta.rebuild((t.id, N.words(t.symbol, t.name), (t.dex or {}).get("f_vol_1h") or t.lists.get("g_vol_1h"))
                              for t in self.toks.values() if now - t.scope_ts < 3600)
            if now - self.context_ts > 600:
                self.refresh_context(now)
            for tok in list(self.toks.values()):
                if tok.lists_ts > tok.eval_ts and tok.lists:
                    try:
                        self.evaluate(tok, now)
                    except Exception:   # noqa: BLE001
                        log.exception("evaluate %s failed", tok.id)
                    n += 1
                    if n % 50 == 0:
                        await asyncio.sleep(0)
            self.db.commit()
            # the expensive look: only the best, only when GMGN's queue is short (the key is shared)
            hour = sum(1 for t in self.deep_times if now - t < 3600)
            cands = sorted((t for t in self.toks.values()
                            if t.score is not None and not t.gate and t.score >= d.min_score and self.in_scope(t.mcap)
                            and now - t.scope_ts < 1800 and now - t.deep_ts >= d.cooldown_h * 3600 and t.id not in self.open_tokens()),
                           key=lambda t: -t.score)
            for tok in cands[:max(0, min(d.per_cycle, d.max_per_hour - hour))]:
                if not self.gmgn.enabled or self.gmgn.backlog_s() > 25:
                    break
                try:
                    await self.deep_look(tok)
                except Exception as e:   # noqa: BLE001
                    log.exception("deep look %s failed", tok.id)
                    self.errors.append((time.time(), f"deep look: {type(e).__name__}"))
            await asyncio.sleep(30)

    def refresh_context(self, now: float) -> None:
        """Chain rotation and launchpad health from this program's own records: where the watched volume is, and
        how often recent snapshots on each chain / launchpad got a fast hit. Past outcomes only."""
        self.context_ts = now
        vol: dict[str, float] = {}
        for t in self.toks.values():
            vol[t.chain] = vol.get(t.chain, 0.0) + float((t.dex or {}).get("f_vol_1h") or t.lists.get("g_vol_1h") or 0.0)
        total = sum(vol.values())
        ctx = {c: {"m_chain_vol_share": v / total} for c, v in vol.items()} if total > 0 else {}
        for r in self.db.query("SELECT t.chain c, COUNT(*) n, AVG(s.fast_hit) r FROM snapshots s JOIN tokens t ON t.id=s.token_id "
                               "WHERE s.fast_hit IS NOT NULL AND s.ts>? GROUP BY t.chain", (now - 3 * 86400,)):
            if r["n"] >= 30:
                ctx.setdefault(r["c"], {})["m_chain_fast_rate_3d"] = r["r"]
        self.context = ctx
        self.pad_rate = {r["p"] or "none": r["r"] for r in self.db.query(
            "SELECT t.launchpad p, COUNT(*) n, AVG(s.fast_hit) r FROM snapshots s JOIN tokens t ON t.id=s.token_id "
            "WHERE s.fast_hit IS NOT NULL AND s.ts>? GROUP BY t.launchpad", (now - 7 * 86400,)) if r["n"] >= 20}
        self.meta.set_winners((r["symbol"], r["name"]) for r in self.db.query(
            "SELECT DISTINCT t.symbol, t.name FROM snapshots s JOIN tokens t ON t.id=s.token_id "
            "WHERE (s.fast_hit=1 OR s.hit=1) AND s.ts>?", (now - 7 * 86400,)))

    async def loop_safety(self) -> None:
        s = self.cfg.trade.safety
        while True:
            await asyncio.sleep(30)
            now = time.time()
            for p in list(self.positions.values()):
                if now - self.pos_safety.get(p.call_id, 0) < s.check_every_s:
                    continue
                self.pos_safety[p.call_id] = now
                tok = self.toks.get(p.token_id)
                if not tok:
                    continue
                f: dict = {}
                info = await self.gmgn.info(tok.chain, tok.address)
                if info:
                    F.put(f, F.from_info(info, tok.address))
                sec = await self.gmgn.security(tok.chain, tok.address)
                if sec:
                    F.put(f, F.from_security(sec))
                now = time.time()
                sig = self.db.query("SELECT type, trigger_at FROM gm_signals WHERE address=? AND trigger_at>?", (tok.address, p.ts))
                F.put(f, F.from_signals([(r["type"], r["trigger_at"]) for r in sig], now))
                tr = self.db.query("SELECT kind, maker, side, usd, price, closing, ts FROM sm_trades WHERE address=? AND ts>?",
                                   (tok.address, max(p.ts, now - 6 * 3600)))
                F.put(f, F.from_feed([tuple(r) for r in tr], now, tok.price))
                if now - tok.dex_ts > 300 and f.get("_price"):
                    self.on_price(tok, f["_price"], f.get("_mcap"), f.get("l_liq_usd"), now)
                ev = p.safety(f, now, self.cfg.trade)
                if ev:
                    await self.handle_events(p, tok, ev, now)

    async def loop_market(self) -> None:
        while True:
            await self.market.refresh()
            try:
                await self.social.refresh_llama()
            except Exception:   # noqa: BLE001
                log.exception("DefiLlama refresh failed")
            await asyncio.sleep(self.cfg.sources.market.every_s)

    async def loop_maintain(self) -> None:
        w, last_prune = self.cfg.watch, 0.0
        while True:
            await asyncio.sleep(20)
            now = time.time()
            try:
                self.flush(now)
                hot = self.open_tokens()
                for tok in list(self.toks.values()):
                    idle = now - max(tok.scope_ts, tok.first_seen) > w.drop_after_h * 3600
                    if not tok.follows and tok.id not in hot and idle:
                        self.credit(tok.id, tok.creator, tok.liq, now)
                        del self.toks[tok.id]
                    elif tok.follows and tok.price_ts and now - tok.price_ts > 86400:
                        for fl in tok.follows:              # no price from any source for a day: nothing more to learn
                            fl.finish("lost", now)
                            self.finish_follow(tok, fl, now)
                        tok.follows = []
                if now - last_prune > 3600:
                    last_prune = now
                    self.prune(now)
                await self.daily_summary(now)
                self.db.commit()
            except Exception:   # noqa: BLE001
                log.exception("maintenance failed")

    def credit(self, token_id: str, creator: str, liq: float | None, now: float) -> None:
        """Every snapshot of a token is graded: credit its early buyers and its deployer, once."""
        r = self.db.one("SELECT COUNT(*) n, MIN(done) done, MAX(hit) hit, MAX(peak_mult) peak, MIN(end_mult) low FROM snapshots "
                        "WHERE token_id=?", (token_id,))
        t = self.db.one("SELECT credited FROM tokens WHERE id=?", (token_id,))
        if not r or not r["n"] or not r["done"] or (t and t["credited"]):
            return
        dead = (liq is not None and liq < self.cfg.label.dead_liq_usd) or (r["low"] is not None and r["low"] < 0.15)
        self.history.credit(token_id, creator or "", bool(r["hit"]), bool(dead), r["peak"] or 0.0, now)

    def prune(self, now: float) -> None:
        s = self.cfg.storage
        self.db.delete_batch("snapshots", "ts<? AND called=0", (now - s.snapshot_days * 86400,))
        self.db.delete_batch("bars", "ts<?", (now - s.bar_days * 86400,))
        self.db.delete_batch("gm_signals", "seen_ts<?", (now - 14 * 86400,))
        self.db.delete_batch("sm_trades", "ts<?", (now - 14 * 86400,))
        self.db.delete_batch("alerts", "ts<?", (now - s.event_days * 86400,))
        self.db.delete_batch("tokens", "last_seen<? AND id NOT IN (SELECT token_id FROM snapshots) "
                             "AND id NOT IN (SELECT token_id FROM calls)", (now - 3 * 86400,))
        if self.db.size_bytes() > s.max_gb * 0.9e9:
            self.db.delete_batch("snapshots", "called=0 AND id IN (SELECT id FROM snapshots WHERE done=1 ORDER BY ts LIMIT 5000)", ())
        for r in self.db.query("SELECT t.id, t.creator, t.last_liq FROM tokens t WHERE t.credited=0 AND t.last_seen<? AND EXISTS "
                               "(SELECT 1 FROM snapshots s WHERE s.token_id=t.id) AND NOT EXISTS (SELECT 1 FROM snapshots s "
                               "WHERE s.token_id=t.id AND s.done=0) LIMIT 500", (now - 3600,)):
            if r["id"] not in self.toks:               # graded while this program was not watching it (e.g. a restart)
                self.credit(r["id"], r["creator"], r["last_liq"], now)
        self.db.reclaim()
        self.history.refresh_base()

    async def daily_summary(self, now: float) -> None:
        at = str(self.cfg.telegram.get("daily_summary_utc") or "off")
        if ":" not in at:
            return
        day = time.strftime("%Y-%m-%d", time.gmtime(now))
        if time.strftime("%H:%M", time.gmtime(now)) < at or self.db.get("summary_day") == day:
            return
        self.db.put("summary_day", day)
        since = now - 86400
        opened = self.db.one("SELECT COUNT(*) n FROM calls WHERE ts>?", (since,))["n"]
        closed = self.db.query("SELECT exit_mult, peak_mult FROM calls WHERE status='closed' AND exit_ts>?", (since,))
        snaps = self.db.one("SELECT COUNT(*) n FROM snapshots WHERE ts>?", (since,))["n"]
        graded = self.db.one("SELECT COUNT(*) n, SUM(hit) hit FROM snapshots WHERE done=1 AND done_ts>?", (since,))
        lines = ["📊 <b>Gem Hunter — last 24 h</b>",
                 f"{len(self.toks)} tokens watched · {snaps} snapshots · {graded['n']} graded ({graded['hit'] or 0} hit "
                 f"{self.cfg.label.target_mult:g}x)",
                 f"Calls opened: {opened} · open now: {len(self.positions)}"]
        if closed:
            wins = sum(1 for r in closed if (r["exit_mult"] or 0) >= 1)
            avg = sum(r["exit_mult"] or 0 for r in closed) / len(closed)
            lines.append(f"Calls closed: {len(closed)} · {wins} in profit · average result {avg:.2f}x · best peak "
                         f"{max(r['peak_mult'] or 0 for r in closed):.1f}x")
        for p in self.positions.values():
            tok = self.toks.get(p.token_id)
            lines.append(f"• {esc(tok.symbol if tok else p.token_id)}: {p.last:.2f}x (peak {p.peak:.2f}x)")
        lines.append(f"Deciding: {'model ' + esc(self.model.ver) if self.model else 'scorecard (learning)'}")
        await self.telegram.send("\n".join(lines), dedupe=f"summary:{day}", kind="summary", silent=True)

    # ------------------------------------------------------------------ learning
    async def retrain(self, promote: bool) -> dict:
        champion = self.db.one("SELECT path FROM models WHERE active=1 ORDER BY id DESC LIMIT 1")
        self.flush(time.time())
        report = await asyncio.to_thread(train, self.db.path, dict(self.cfg.learn), dict(self.cfg.call),
                                         self.cfg.data_dir / "models", champion["path"] if champion else None)
        lift = report.pop("lift", None)
        if lift:
            self.db.put("lift", lift[:60])
        report["would_promote"] = bool(report.get("promoted"))
        if report.get("promoted") and promote:
            self.db.execute("UPDATE models SET active=0")
            self.db.execute("INSERT INTO models(ver, ts, n_rows, n_pos, metrics, path, active) VALUES(?,?,?,?,?,?,1)",
                            (report["ver"], report["ts"], report["n_rows"], report["n_pos"],
                             json.dumps({k: report.get(k) for k in ("test", "threshold", "importance")}), report["path"]))
            self.model = Model.load(report["path"]) or self.model
            log.info("model %s promoted", report["ver"])
        else:
            report["promoted"] = False
        self.last_train = report
        self.db.put("last_train", report)
        self.db.put("last_train_ts", time.time())
        self.db.commit()
        log.info("retrain: %s", report.get("note") or report.get("verdict") or "done")
        return report

    async def retune(self) -> dict:
        """Re-weight the scorecard from the grades so far (tuning.py). Small steps, from the first winners on."""
        self.flush(time.time())
        t = await asyncio.to_thread(tune, self.db.path, dict(self.cfg.score.weights), dict(self.cfg.learn.tune))
        if t.get("items"):
            self.tuning = t
            self.db.put("tuning", t)
        self.db.put("tuning_last", {"ts": t["ts"], "n": t["n"], "pos": t["pos"], "note": t.get("note")})
        self.db.commit()
        log.info("scorecard tuning: %s", t.get("note"))
        return t

    async def loop_tune(self) -> None:
        every = float(self.cfg.learn.tune.every_h) * 3600
        while True:
            await asyncio.sleep(300)
            if not self.cfg.learn.tune.enabled:
                continue
            last = self.db.get("tuning_last") or {}
            # until the first tuning exists, look every half hour: it starts as soon as the first winners are graded
            if time.time() - (last.get("ts") or 0) < (every if self.tuning else min(every, 1800)):
                continue
            try:
                await self.retune()
            except Exception:   # noqa: BLE001
                log.exception("scorecard tuning failed")
                self.db.put("tuning_last", {"ts": time.time(), "note": "failed"})

    async def loop_learn(self) -> None:
        while True:
            await asyncio.sleep(600)
            L = self.cfg.learn
            if time.time() - (self.db.get("last_train_ts", 0) or 0) < L.retrain_hours * 3600 or self.retraining:
                continue
            self.retraining = {"started": time.time(), "promote": bool(L.auto_retrain), "auto": True}
            try:
                await self.retrain(promote=bool(L.auto_retrain))
            except Exception:   # noqa: BLE001
                log.exception("retrain failed")
                self.db.put("last_train_ts", time.time())
            finally:
                self.retraining = False

    # ------------------------------------------------------------------ run
    def health(self) -> dict:
        now = time.time()
        g = self.gmgn
        return {
            "version": os.environ.get("APP_VERSION", "dev")[:7], "uptime_s": now - self.started,
            "watching": len(self.toks), "in_scope": sum(1 for t in self.toks.values() if now - t.scope_ts < 1800),
            "following": sum(len(t.follows) for t in self.toks.values()), "open_calls": len(self.positions),
            "by_chain": {c: sum(1 for t in self.toks.values() if t.chain == c) for c in self.cfg.chains},
            "last_sweep_s": {c: round(now - ts) for c, ts in self.sweep_ts.items()},
            "counts": self.counts, "model": self.model.ver if self.model else None,
            "deciding": "model" if self.model else "tuned scorecard" if self.tuning else "scorecard",
            "call_threshold": self.call_threshold(now), "tuning": self.db.get("tuning_last"),
            "gmgn": {"enabled": g.enabled, "calls": g.calls, "weight_spent": g.spent, "errors": g.errors, "limited": g.limited,
                     "remaining": g.remaining, "backlog_s": round(g.backlog_s(), 1), "last_limit": g.last_limit,
                     "skipped_in_ban": g.skipped,
                     "budget_per_s": g.rate, "heavy_paused_s": max(0, round(g.heavy_until - now)),
                     "unsupported": sorted(f"{p} ({c})" for p, c in g.unsupported)},
            "apis": {k: {"calls": v.calls, "errors": v.errors, "limited": v.limited} for k, v in self.http.limiters.items()},
            "telegram": {"enabled": self.telegram.enabled, "sent": self.telegram.sent, "failed": self.telegram.failed},
            "db_mb": round(self.db.size_bytes() / 1e6, 1),
            "errors": [{"ts": t, "what": w} for t, w in self.errors],
        }

    async def run(self, once_s: float | None = None) -> None:
        from .web.server import serve
        if not self.gmgn.enabled:
            log.error("GMGN_API_KEY is not set: no tokens can be found. Open calls and unfinished snapshots are still followed.")
        loops = [self.loop_rank, self.loop_trenches, self.loop_signals, self.loop_feed, self.loop_prices, self.loop_evaluate,
                 self.loop_safety, self.loop_market, self.loop_maintain, self.loop_learn, self.loop_tune]

        async def guard(fn):
            while True:
                try:
                    await fn()
                    return
                except asyncio.CancelledError:
                    raise
                except Exception as e:   # noqa: BLE001 - one failing loop must not take the others down
                    log.exception("%s crashed - restarting it in 30 s", fn.__name__)
                    self.errors.append((time.time(), f"{fn.__name__}: {type(e).__name__}"))
                    await asyncio.sleep(30)
        self._tasks = [asyncio.create_task(guard(fn)) for fn in loops] + [asyncio.create_task(serve(self))]
        try:
            if once_s:
                await asyncio.sleep(once_s)
            else:
                await asyncio.gather(*self._tasks)
        finally:
            for t in self._tasks:
                t.cancel()
            await asyncio.gather(*self._tasks, return_exceptions=True)
            self.flush(time.time())
            await self.gmgn.close()
            await self.http.close()
            await self.telegram.close()
            self.db.close()
