"""The whole loop on canned data: discover -> snapshot -> deep look -> call -> updates -> exit -> grade ->
research export -> dashboard API -> retrain. No network."""
import asyncio
import gzip
import json
import random
import time

from fastapi.testclient import TestClient

from gemhunter import factors as F
from gemhunter.export import build_day, list_days
from gemhunter.model import train
from gemhunter.trade import Follow
from gemhunter.web.server import build_app

from .helpers import ADDR, CREATOR, make_engine, rank_item

TID = f"sol:{ADDR}"


async def discover(eng, now=None):
    now = now or time.time()
    for i in await eng.gmgn.rank("sol"):
        tok = eng.touch("sol", i["address"], F.from_rank(i, "1h"), now)
        if tok:
            tok.symbol, tok.name = i["symbol"], i["name"]
    return eng.toks.get(TID)


async def settle():
    for _ in range(5):
        await asyncio.sleep(0)


async def test_full_cycle_call_updates_exit(tmp_path):
    eng = make_engine(tmp_path)
    tok = await discover(eng)
    assert tok and tok.price == 0.0003 and tok.mcap == 300000
    assert eng.touch("sol", "Big", F.from_rank(rank_item("Big", market_cap=5e6), "1h"), time.time()) is None   # out of range
    eng.evaluate(tok, time.time())
    assert tok.gate == [] and tok.score > 60 and tok.n_snaps == 1 and len(tok.follows) == 1
    eng.evaluate(tok, time.time())
    assert tok.n_snaps == 1                                                 # one snapshot per 6 hours

    await eng.deep_look(tok)
    call = eng.db.one("SELECT * FROM calls")
    assert call and call["status"] == "open" and abs(call["entry_price"] - 0.0003 * 1.03) < 1e-12
    assert len(eng.positions) == 1 and len(tok.follows) == 2
    kind, text, _, _ = eng.telegram.messages[0]
    assert kind == "entry" and "GEM CALL" in text and ADDR in text and "Plan: stop -40%" in text
    assert eng.db.one("SELECT COUNT(*) n FROM token_wallets")["n"] == 40   # early buyers kept for the proven-wallet list
    f = eng.db.unpack(*eng.db.one("SELECT fkeys, features FROM snapshots WHERE stage='deep'"))
    assert f["s_checked"] == 1 and f["w_smart_bought_n"] == 4 and f["d_created_n"] == 2 and f["p_candles_n"] == 60
    assert f["stage"] == "deep" and f["chain"] == "sol" and not any(k.startswith("_") for k in f)

    await eng.deep_look(tok)                                                # a second look does not call again
    assert eng.db.one("SELECT COUNT(*) n FROM calls")["n"] == 1

    now = time.time()
    eng.on_price(tok, 0.00065, 650000, 70000, now + 600)                    # 2.1x
    await settle()
    eng.on_price(tok, 0.00125, 1250000, 90000, now + 1200)                  # 4x and past $1M
    await settle()
    labels = [m[1].split("\n")[1] for m in eng.telegram.messages if m[0] == "update"]
    assert labels == ["2x reached", "3x reached", "market cap passed $1M"] or "2x reached" in labels
    assert all(m[2] == {"chat": 1} for m in eng.telegram.messages[1:])       # every update replies under the entry
    eng.on_price(tok, 0.00055, 550000, 90000, now + 1800)                   # 56% off the peak
    eng.on_price(tok, 0.00055, 550000, 90000, now + 1820)
    await settle()
    call = eng.db.one("SELECT * FROM calls")
    assert call["status"] == "closed" and "trailing stop" in call["exit_reason"] and not eng.positions
    assert abs(call["exit_mult"] - 0.00055 / (0.0003 * 1.03) * 0.97) < 1e-6 and call["peak_mult"] > 4
    assert eng.telegram.messages[-1][0] == "exit" and "EXIT" in eng.telegram.messages[-1][1]
    kinds = [r["kind"] for r in eng.db.query("SELECT kind FROM call_events ORDER BY id")]
    assert kinds[0] == "entry" and kinds[-1] == "exit" and kinds.count("update") >= 2
    assert eng.db.one("SELECT n_called, n_rug FROM deployers WHERE addr=?", (CREATOR,))["n_called"] == 1

    # the snapshots are graded too: 3x was reached before any stop
    assert all(fl.hit for fl in tok.follows)
    eng.on_price(tok, 0.0005, 500000, 90000, now + eng.cfg.label.horizon_h * 3600 + 60)
    snaps = eng.db.query("SELECT done, hit, peak_mult, end_reason FROM snapshots")
    assert len(snaps) == 3 and all(s["done"] and s["hit"] and s["peak_mult"] > 4 for s in snaps) and not tok.follows
    eng.credit(tok.id, tok.creator, tok.liq, now)
    assert eng.db.one("SELECT COUNT(*) n, SUM(n_win) w FROM wallets")["w"] == 40
    eng.flush(time.time())
    eng.db.close()


async def test_gated_token_is_snapshotted_but_never_called(tmp_path):
    eng = make_engine(tmp_path)
    eng.gmgn.rank_items = {"sol": [rank_item(bundler_rate=0.5)]}
    tok = await discover(eng)
    eng.evaluate(tok, time.time())
    assert any("bundlers" in g for g in tok.gate) and tok.n_snaps == 1      # still recorded: the learner sees rejects too
    await eng.deep_look(tok)
    assert eng.db.one("SELECT COUNT(*) n FROM calls")["n"] == 0 and not eng.telegram.messages
    assert eng.db.one("SELECT gate FROM snapshots WHERE stage='deep'")["gate"]
    eng.db.close()


async def test_safety_exit_and_deployer_block(tmp_path):
    eng = make_engine(tmp_path)
    eng.gmgn.rank_items = {"sol": [rank_item(creator_token_status="creator_hold", creator_close=False, dev_team_hold_rate=0.03)]}
    eng.gmgn.info_over = {"dev": {"creator_address": CREATOR, "creator_token_status": "hold"}}
    eng.gmgn.sec["creator_token_status"] = "creator_hold"
    tok = await discover(eng)
    eng.evaluate(tok, time.time())
    await eng.deep_look(tok)
    p = next(iter(eng.positions.values()))
    assert p.dev_closed_at_entry == 0
    ev = p.safety({"d_closed": 1}, time.time(), eng.cfg.trade)
    await eng.handle_events(p, tok, ev, time.time())
    call = eng.db.one("SELECT * FROM calls")
    assert call["status"] == "closed" and call["exit_reason"] == "dev sold out"
    assert eng.db.one("SELECT n_rug FROM deployers WHERE addr=?", (CREATOR,))["n_rug"] == 1
    f = eng.factors(tok, time.time())
    assert f["own_dev_rug_n"] == 1 and "deployer rugged an earlier call" in eng.judge(f)[0]
    eng.db.close()


async def test_restart_resumes_open_call_and_unfinished_snapshots(tmp_path):
    eng = make_engine(tmp_path)
    tok = await discover(eng)
    eng.evaluate(tok, time.time())
    await eng.deep_look(tok)
    eng.on_price(tok, 0.00066, 660000, 70000, time.time() + 60)
    await settle()
    eng.flush(time.time())
    eng.db.close()
    again = make_engine(tmp_path)
    assert len(again.positions) == 1 and TID in again.toks and len(again.toks[TID].follows) == 2
    p = next(iter(again.positions.values()))
    assert p.armed and "x2" in p.sent and p.peak > 2                         # the 2x update is not sent a second time
    again.on_price(again.toks[TID], 0.00067, 670000, 70000, time.time() + 120)
    await settle()
    assert not again.telegram.messages
    again.db.close()


async def test_call_caps(tmp_path):
    eng = make_engine(tmp_path, "call.max_per_day=1")
    eng.gmgn.rank_items = {"sol": [rank_item(), rank_item("Second2222222222222222222222222222222222222", symbol="TWO")]}
    await discover(eng)
    for tok in list(eng.toks.values()):
        eng.evaluate(tok, time.time())
        await eng.deep_look(tok)
    assert eng.db.one("SELECT COUNT(*) n FROM calls")["n"] == 1 and eng.db.one("SELECT COUNT(*) n FROM snapshots WHERE stage='deep'")["n"] == 2
    eng.db.close()


async def test_export_and_dashboard_api(tmp_path):
    eng = make_engine(tmp_path)
    tok = await discover(eng)
    eng.evaluate(tok, time.time())
    await eng.deep_look(tok)
    now = time.time()
    eng.on_price(tok, 0.0001, 100000, 60000, now + 60)
    eng.on_price(tok, 0.0001, 100000, 60000, now + 90)                      # -67%: stop
    await settle()
    eng.flush(time.time())
    days = list_days(eng.db.path)
    assert days and days[0]["snapshots"] == 2 and days[0]["calls"] == 1
    rows = [json.loads(x) for x in gzip.decompress(build_day(eng.db.path, days[0]["name"][:10])).splitlines()]
    kinds = [r["kind"] for r in rows]
    assert kinds.count("snapshot") == 2 and kinds.count("call") == 1
    call = next(r for r in rows if r["kind"] == "call")
    assert call["exit_reason"] == "stop -40%" and [e["kind"] for e in call["events"]] == ["entry", "exit"] and "tg_msg" not in call
    snap = next(r for r in rows if r["kind"] == "snapshot")
    assert snap["stopped"] == 1 and snap["hit"] == 0 and snap["f"]["chain"] == "sol"

    c = TestClient(build_app(eng))
    assert c.get("/").status_code == 200 and "Gem Hunter" in c.get("/").text
    calls = c.get("/api/calls").json()
    assert len(calls) == 1 and calls[0]["symbol"] == "GEM" and calls[0]["status"] == "closed" and calls[0]["result_mult"] < 0.6
    detail = c.get(f"/api/call?id={calls[0]['id']}").json()
    assert [e["kind"] for e in detail["events"]] == ["entry", "exit"] and detail["factors"]["s_checked"] == 1
    assert c.get("/api/candidates").json()[0]["symbol"] == "GEM"
    sb = c.get("/api/scoreboard").json()
    assert sb["total"]["n"] == 1 and sb["total"]["closed"] == 1 and sb["by_exit"][0]["exit_reason"] == "stop -40%"
    assert c.get("/api/learning").json()["graded"]["n"] == 2
    assert c.get("/api/health").json()["watching"] == 1
    assert c.get("/api/research").json()["days"][0]["calls"] == 1
    assert c.get("/api/research/file/" + days[0]["name"]).status_code == 200
    assert c.get("/api/research/file/..%2Fgems.db").status_code == 404
    assert c.get(f"/api/token?id={TID}").json()["token"]["symbol"] == "GEM"
    eng.db.close()


def test_dashboard_requires_login_off_localhost(tmp_path, monkeypatch):
    monkeypatch.setenv("DASHBOARD_PASSWORD", "correct horse battery")
    eng = make_engine(tmp_path, "dashboard.bind_host=0.0.0.0")
    c = TestClient(build_app(eng))
    assert c.get("/api/calls").status_code == 401 and c.get("/api/research").status_code == 401
    assert c.post("/api/login", json={"password": "nope"}).status_code == 401
    assert c.post("/api/login", json={"password": "correct horse battery"}).status_code == 200
    assert c.get("/api/calls").status_code == 200
    eng.db.close()


async def test_learner_trains_tests_and_promotes(tmp_path):
    """Snapshots whose outcome depends on two factors: the model must find them, beat the scorecard and be promoted."""
    eng = make_engine(tmp_path, "learn.min_rows=400;learn.min_pos=30")
    rng, t0 = random.Random(3), time.time() - 40 * 86400
    for i in range(1500):
        bundler, smart = rng.uniform(0, 30), rng.randint(0, 8)
        p = 0.02 + (0.5 if bundler < 6 and smart >= 4 else 0.0)
        hit = int(rng.random() < p)
        kid, vals = eng.db.pack({"chain": "sol", "stage": "scan", "h_bundler_pct": bundler, "w_smart_n": smart, "noise": rng.random()})
        eng.db.execute("INSERT INTO snapshots(token_id, ts, stage, price, entry_price, mcap, fkeys, features, score, done, hit, peak_mult, "
                       "fast_hit) VALUES(?,?,?,?,?,?,?,?,?,1,?,?,?)", (f"sol:t{i}", t0 + i * 2000, "scan", 1, 1.03, 3e5, kid, vals,
                                                                     rng.uniform(40, 70), hit, 3.5 if hit else 1.2, hit))
    eng.db.commit()
    report = await eng.retrain(promote=True)
    assert report["promoted"] and eng.model is not None, report.get("note") or report.get("verdict")
    assert report["test"]["model"]["ap"] > 2 * report["test"]["scorecard"]["ap"]
    lift = eng.db.get("lift")
    assert lift[0]["n"] == 1500 and {"h_bundler_pct", "w_smart_n"} & {r["feature"] for r in lift[1:3]}
    def avg(bundlers, smarts):
        ps = [eng.model.predict({"chain": "sol", "stage": "scan", "h_bundler_pct": b, "w_smart_n": s, "noise": 0.5})
              for b in bundlers for s in smarts]
        return sum(ps) / len(ps)
    good, bad = avg((1, 3, 5), (4, 6, 8)), avg((15, 25), (0, 2))
    assert good > 0.25 and good > 5 * bad
    # with a model in use, the call decision is its probability against its threshold
    tok = await discover(eng)
    assert eng.call_block(tok, 50, 0.9, time.time()) is None and "chance" in eng.call_block(tok, 99, 0.01, time.time())
    assert train(eng.db.path, dict(eng.cfg.learn), dict(eng.cfg.call), tmp_path / "m2", None)["n_rows"] == 1500
    eng.db.close()


async def test_scorecard_tunes_itself_from_fast_grades(tmp_path):
    """Snapshots where few bundlers really marks the winners: the tuner must raise that item, leave noise alone,
    and the engine must score with the new weights."""
    eng = make_engine(tmp_path)
    rng, t0 = random.Random(5), time.time() - 2 * 86400
    for i in range(400):
        bundler = rng.uniform(0, 30)
        fast = int(rng.random() < (0.45 if bundler < 6 else 0.03))
        kid, vals = eng.db.pack({"chain": "sol", "stage": "scan", "h_bundler_pct": bundler, "h_insider_pct": rng.uniform(0, 30)})
        eng.db.execute("INSERT INTO snapshots(token_id, ts, stage, price, entry_price, mcap, fkeys, features, score, fast_hit) "
                       "VALUES(?,?,?,?,?,?,?,?,?,?)", (f"sol:t{i}", t0 + i * 300, "scan", 1, 1.03, 3e5, kid, vals, 60, fast))
    eng.db.commit()
    t = await eng.retune()
    assert t["pos"] >= 30 and "tuned on" in t["note"]
    assert t["items"]["few bundlers"]["auc"] > 0.75 and t["items"]["few bundlers"]["mult"] > 1.3
    assert 0.75 < t["items"]["few insiders"]["mult"] < 1.25                     # noise stays near 1
    assert t["items"]["smart holders bought"]["mult"] == 1.0                    # never measured: untouched
    assert eng.tuning is t and eng.db.get("tuning")["items"]["few bundlers"]["mult"] > 1.3
    f = {"chain": "sol", "h_bundler_pct": 28.0, "h_insider_pct": 1.0}
    eng.tuning = {}
    before = eng.judge(f)[1]
    eng.tuning = t
    assert eng.judge(f)[1] < before                                             # heavy bundling now costs more
    eng.db.close()


async def test_tuning_waits_for_winners(tmp_path):
    eng = make_engine(tmp_path)
    kid, vals = eng.db.pack({"chain": "sol", "h_bundler_pct": 3.0})
    for i in range(80):
        eng.db.execute("INSERT INTO snapshots(token_id, ts, stage, fkeys, features, score, fast_hit) VALUES(?,?,?,?,?,?,?)",
                       (f"sol:t{i}", time.time() - i * 60, "scan", kid, vals, 60, int(i < 3)))
    eng.db.commit()
    t = await eng.retune()
    assert "collecting: 3/5 winners" in t["note"] and not eng.tuning and eng.db.get("tuning_last")["pos"] == 3
    eng.db.close()


async def test_deep_look_adds_community_theme_and_original(tmp_path):
    eng = make_engine(tmp_path)
    eng.gmgn.rank_items = {"sol": [rank_item(symbol="AGENT", name="AI Agent Cat"),
                                   rank_item("Copy3333333333333333333333333333333333333333", symbol="agent", name="ai agent kitty",
                                             creation_timestamp=time.time() - 3600)]}
    now = time.time()
    for i in eng.gmgn.rank_items["sol"]:
        tok = eng.touch("sol", i["address"], F.from_rank(i, "1h"), now)
        tok.symbol, tok.name = i["symbol"], i["name"]
    eng.flush(now)
    first, copy = list(eng.toks.values())
    eng.meta.rebuild((t.id, eng_words(t), 9000) for t in eng.toks.values())
    f1, f2 = eng.factors(first, now), eng.factors(copy, now)
    assert f1["narrative"] == "ai" and f1["a_symbol_twins_n"] == 1 and f1["a_symbol_oldest"] == 1 and f2["a_symbol_oldest"] == 0
    assert f1["m_meta_heat"] == 1.0 and f1["m_meta_peers_n"] == 1 and f1["a_symbol_len"] == 5
    eng.gmgn.info_over = {"symbol": "AGENT", "name": "AI Agent Cat"}
    await eng.deep_look(first)
    f = eng.db.unpack(*eng.db.one("SELECT fkeys, features FROM snapshots WHERE stage='deep'"))
    assert f["a_tg_members"] == 2400 and f["a_web_domain_age_d"] == 40 and f["fu_claims_product"] == 1 and f["narrative"] == "ai"
    text = eng.telegram.messages[0][1]
    assert "Telegram 2,400 (120 online)" in text and "theme: ai" in text and "original ticker" in text and "own site" in text
    eng.db.close()


def eng_words(tok):
    from gemhunter import narrative as N
    return N.words(tok.symbol, tok.name)


async def test_deep_look_completes_while_gmgn_is_banned(tmp_path):
    """GMGN answers nothing for the token (a ban): the look goes on with the list figures, free candles and the
    community sources, records which parts were missing, and the list's own contract check still allows a call."""
    from .helpers import candles
    eng = make_engine(tmp_path)

    async def nothing(*a, **k):
        return None
    for name in ("info", "security", "kline", "holders", "traders", "created_tokens"):
        setattr(eng.gmgn, name, nothing)

    async def free_candles(chain, pool, hours=168):
        return candles()
    eng.gecko.candles = free_candles
    tok = await discover(eng)
    eng.evaluate(tok, time.time())
    await eng.deep_look(tok)
    f = eng.db.unpack(*eng.db.one("SELECT fkeys, features FROM snapshots WHERE stage='deep'"))
    assert f["deep_page"] == 0 and f["deep_holders"] == 0 and f["deep_candles"] == 1 and f["p_candles_n"] == 60
    assert f["s_list_check"] == 1 and "s_checked" not in f and f["a_tg_members"] == 2400
    snap = eng.db.one("SELECT gate, score FROM snapshots WHERE stage='deep'")
    assert snap["gate"] is None and snap["score"] > 60                   # not gated; scored on what was available
    # less evidence, lower score: it is called only if it still clears the bar every call must clear
    assert eng.db.one("SELECT COUNT(*) n FROM calls")["n"] == int(snap["score"] >= eng.call_threshold(time.time()))
    eng.db.close()


def test_bad_quotes_are_never_graded(tmp_path):
    """A quote in the wrong unit (seen on Meteora curve tokens in GMGN's graduated list) must not become a snapshot,
    and one freak tick must not make a hit."""
    from .helpers import rank_item
    eng = make_engine(tmp_path)
    tr = F.from_trench({**rank_item(), "market_cap": 236000, "liquidity": 0.0023, "price": 3.8e-12})
    assert "_price" not in tr and "l_liq_usd" not in tr
    assert F.from_trench({**rank_item(), "market_cap": 236000, "liquidity": 50000})["l_liq_usd"] == 50000
    tok = eng.touch("sol", ADDR, F.from_rank(rank_item(), "1h"), time.time())
    assert eng.price_ok(tok, 0.0003, 300000) and not eng.price_ok(tok, 3.8e-12, 300000)
    f = eng.factors(tok, time.time())
    assert eng.snapshot(tok, {**f, "_price": 3.8e-12}, "scan", 60, None, [], time.time()) is None
    assert eng.snapshot(tok, {**f, "l_liq_usd": 0.002}, "scan", 60, None, [], time.time()) is None
    L = eng.cfg.label
    fl = Follow(1, 1.0, 0.0)
    fl.tick(1.1, 50000, 60, L)
    fl.tick(60_000_000.0, 50000, 120, L)                 # one freak print
    assert not fl.hit and fl.peak < 2 and fl.fast is None
    fl.tick(1.2, 50000, 180, L)
    assert fl.peak < 2
    g = Follow(2, 1.0, 0.0)
    g.tick(40.0, 50000, 60, L)
    g.tick(41.0, 50000, 90, L)                           # confirmed on the next poll: a real move
    assert g.hit and g.peak == 41.0
    eng.db.close()


def test_bad_quote_migration(tmp_path):
    from gemhunter.db import DB
    db = DB(tmp_path / "gems.db")
    db.execute("DELETE FROM kv WHERE k='migr_bad_quotes_v1'")
    for i, (liq, peak) in enumerate(((0.002, 6e7), (50000, 2.0), (50000, 5000.0))):
        db.execute("INSERT INTO snapshots(token_id, ts, stage, price, liq, peak_mult) VALUES(?,?,?,?,?,?)", (f"t{i}", 1, "scan", 1, liq, peak))
    db.put("tuning", {"items": {}})
    db.commit()
    db.close()
    db = DB(tmp_path / "gems.db")
    assert db.one("SELECT COUNT(*) n FROM snapshots")["n"] == 1 and db.get("tuning") is None and db.get("migr_bad_quotes_v1") == 2
    db.close()
