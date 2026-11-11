"""Factors, gates, scorecard and the trade plan: pure logic."""
import time

from gemhunter import factors as F
from gemhunter.config import load_config
from gemhunter.scoring import gates, scorecard
from gemhunter.sources.dexscreener import parse_pair
from gemhunter.sources.extra import parse_goplus
from gemhunter.sources.gmgn import rows
from gemhunter.trade import Follow, Position

from .helpers import candles, holder, holders_list, rank_item

CFG = load_config()


def full(**over) -> dict:
    f = {"chain": "sol"}
    F.put(f, F.from_rank(rank_item(**over), "1h"))
    return F.derive(f)


def test_rank_item_becomes_factors():
    f = full()
    assert f["_mcap"] == 300000 and f["h_top10_pct"] == 16 and f["h_bundler_pct"] == 3
    assert f["s_mint_renounced"] == 1 and f["s_lp_burned"] == 1 and f["d_closed"] == 1
    assert "s_honeypot" not in f                       # empty on Solana = not applicable, never "safe"
    assert abs(f["l_liq_to_mcap"] - 0.2) < 1e-9 and 0.5 < f["f_buy_ratio_n_1h"] < 0.6
    assert abs(f["l_slip_1k_pct"] - 1000 / 31000 * 100) < 1e-6
    assert abs(f["p_to_ath"] - 300000 / 520000) < 1e-9 and 29 < f["p_age_h"] < 31
    assert set(F.stored(f)) & {"_mcap", "g_vol_1h"} == set()


def test_gates_pass_and_fail():
    assert gates(full(), CFG.gates, CFG.scope) == []
    assert "market cap outside range" in gates(full(market_cap=900000), CFG.gates, CFG.scope)
    assert any("bundlers" in g for g in gates(full(bundler_rate=0.45), CFG.gates, CFG.scope))
    assert any("rug risk" in g for g in gates(full(rug_ratio=0.4), CFG.gates, CFG.scope))
    assert "wash trading" in gates(full(is_wash_trading=True), CFG.gates, CFG.scope)
    assert "mint authority not revoked" in gates(full(renounced_mint=0), CFG.gates, CFG.scope)
    assert "liquidity not burned or locked" in gates(full(burn_status="none", lock_percent=0.1), CFG.gates, CFG.scope)
    assert any("top 10" in g for g in gates(full(top_10_holder_rate=0.55), CFG.gates, CFG.scope))
    assert "no contract check answered" in gates(full(), CFG.gates, CFG.scope, final=True)
    f = full()
    f.update({"d_created_n": 60, "d_open_ratio": 0.01})
    assert any("serial deployer" in g for g in gates(f, CFG.gates, CFG.scope))


def test_evm_gates():
    f = {"chain": "base", "_mcap": 300000, "l_liq_usd": 50000, "s_open_source": 0}
    assert "contract not verified" in gates(F.derive(f), CFG.gates, CFG.scope)
    f = {"chain": "eth", "_mcap": 300000, "l_liq_usd": 50000, "s_open_source": 1, "s_owner_renounced": 0, "s_lp_locked_pct": 10}
    assert "owner keeps control and liquidity is not locked" in gates(F.derive(f), CFG.gates, CFG.scope)
    gp = parse_goplus({"is_open_source": "1", "is_honeypot": "0", "buy_tax": "0.02", "sell_tax": "0.15", "is_blacklisted": "1",
                       "owner_address": "0xabc", "lp_holders": [{"address": "0x000000000000000000000000000000000000dEaD", "percent": "0.97"}]})
    assert gp["gp_tax_max_pct"] == 15 and gp["gp_blacklist"] == 1 and gp["gp_renounced"] == 0 and gp["gp_lp_locked_pct"] == 97
    f = F.derive({"chain": "bsc", "_mcap": 300000, "l_liq_usd": 50000, **gp})
    g = gates(f, CFG.gates, CFG.scope)
    assert any("tax (GoPlus)" in x for x in g) and any("owner powers" in x for x in g)


def test_scorecard_prefers_clean_tokens():
    good, _, strong, _ = scorecard(full(), CFG.score.weights)
    bad, _, _, weak = scorecard(full(bundler_rate=0.28, rat_trader_amount_rate=0.25, top_10_holder_rate=0.38, smart_degen_count=0,
                                     creator_token_status="creator_hold", creator_close=False, dev_team_hold_rate=0.09,
                                     buys=300, sells=600), CFG.score.weights)
    assert good > bad + 15 and 0 <= bad <= 100 and good <= 100
    assert "few bundlers" in strong and any("few bundlers" in w for w in weak)
    empty, groups, _, _ = scorecard({}, CFG.score.weights)
    assert empty == 50 and set(groups.values()) == {0.5}          # no data = neutral, never good


def test_holders_cost_basis_clusters_and_smart_money():
    hs = holders_list()
    hs.append(holder(90, 0.03, avg_cost=0.00003))                          # up 10x: future sell pressure
    hs.append(holder(91, 0.02, native_transfer={"address": "SameFunder"}))
    hs.append(holder(92, 0.02, native_transfer={"address": "SameFunder"}))
    hs.append(holder(93, 0.02, tags=["smart_degen"], buy_tx_count_cur=0, transfer_in=True))   # sent tokens, never bought
    f = F.from_holders(hs, 0.0003)
    assert f["h_list_n"] == 44                                             # the pool is not a holder
    assert abs(f["c_profit_5x_pct"] - 3) < 1e-6 and abs(f["h_cluster_max_pct"] - 4) < 1e-6
    assert f["w_smart_bought_n"] == 4 and f["w_smart_seeded_n"] == 1 and abs(f["c_transfer_in_pct"] - 2) < 1e-6
    assert abs(f["w_smart_entry_ratio"] - 0.0003 / 0.00028) < 1e-6 and f["h_whale_net_buy_usd"] > 0
    buyers = F.early_buyers(hs, None, 10)
    assert len(buyers) == 10 and buyers[0][0] == "Wallet000" and all(b[0] != "Wallet093" for b in buyers)
    t = F.from_traders(hs, 300000)
    assert t["f_buyers_n"] == 43 and t["c_early_retained_pct"] == 100


def test_candles_and_deployer_history():
    k = F.from_kline(candles(), 0.0003, time.time() - 60 * 3600, 7)
    assert k["p_candles_n"] == 60 and k["p_dd_from_high_pct"] < 5 and k["p_higher_lows_n"] == 3 and "p_mult_from_launch" in k
    dump = candles()
    for c in dump[20:30]:
        c.update(low=c["low"] * 0.2, close=c["close"] * 0.3)
    assert F.from_kline(dump, 0.0003, None, 7)["p_survived_dump"] == 1
    d = F.from_created({"inner_count": 40, "open_count": 1, "tokens": [
        {"token_address": "A", "market_cap": "100", "token_ath_mc": "30000", "create_timestamp": time.time() - 3600},
        {"token_address": "ME", "market_cap": "300000", "token_ath_mc": "500000"}]}, "ME")
    assert d["d_created_n"] == 41 and d["d_prev_n"] == 1 and d["d_prev_dead_pct"] == 100 and d["d_last_create_h"] < 2


def test_smart_feed_convergence_and_divergence():
    now = time.time()
    tr = [("smart", f"w{i}", "buy", 800.0, 0.0002, 0, now - 600 - i * 300) for i in range(3)]
    f = F.from_feed(tr, now, 0.0003)
    assert f["w_sm_converge_n"] == 3 and f["w_sm_net_usd_6h"] == 2400 and abs(f["w_sm_entry_ratio"] - 1.5) < 1e-9
    tr = [("kol", "k1", "buy", 500.0, 0.0003, 0, now - 100), ("smart", "w1", "sell", 2000.0, 0.0003, 1, now - 200)]
    f = F.from_feed(tr, now, 0.0003)
    assert f["w_kol_in_smart_out"] == 1 and f["w_sm_sellers_1h"] == 1
    s = F.from_signals([(12, now - 100), (12, now - 200), (10, now - 50), (7, now - 2 * 86400)], now)
    assert s["w_sig_smart_buy_n"] == 2 and s["h_sig_bundler_sell_n"] == 1 and s["p_sig_ath_n"] == 0


def test_history_holder_growth():
    now = time.time()
    f = F.from_history([(now - 7200, 1000, 20.0, 2e5), (now - 3600, 1100, 19.0, 2.5e5), (now, 1210, 18.0, 3e5)])
    assert 9 < f["h_holder_growth_1h_pct"] < 11 and f["p_watched_h"] == 2


def test_source_parsers():
    assert rows({"rank": [{"a": 1}]}, "rank") == [{"a": 1}] and rows({"data": {"list": [{"a": 1}]}}) == [{"a": 1}]
    assert rows([{"a": 1}, 5]) == [{"a": 1}] and rows(None) == []
    p = parse_pair({"priceUsd": "0.01", "marketCap": 250000, "liquidity": {"usd": 40000}, "volume": {"h1": 9000},
                    "txns": {"h1": {"buys": 10, "sells": 4}}, "pairCreatedAt": 1700000000000})
    assert p["price"] == 0.01 and p["mcap"] == 250000 and p["buys_1h"] == 10 and p["pool_created"] == 1700000000


# ------------------------------------------------------------------ outcomes and the trade plan
def test_follow_hit_stop_horizon():
    L, t0 = CFG.label, 1000.0
    f = Follow(1, 1.0, t0)
    assert not f.tick(1.5, 50000, t0 + 60, L) and not f.tick(3.1, 50000, t0 + 120, L)
    assert f.hit and f.t_hit == 120 and not f.done
    assert f.tick(1.0, 50000, t0 + L.horizon_h * 3600 + 1, L) and f.reason == "horizon" and f.peak == 3.1
    f = Follow(2, 1.0, t0)
    assert f.tick(0.45, 50000, t0 + 60, L) and f.stopped and not f.hit and f.reason == "stopped"
    f = Follow(3, 1.0, t0)
    assert f.tick(None, 0.0, t0 + 60, L) and f.stopped and f.reason == "liquidity gone"


def test_position_ladder_updates_then_trailing_exit():
    P, t0 = CFG.trade, 1000.0
    p = Position(1, "sol:x", t0, 1.0, 300000, 60000)
    assert p.tick(1.5, 450000, 60000, t0 + 60, P) == []
    ev = p.tick(2.2, 660000, 60000, t0 + 120, P)
    assert [e.label for e in ev] == ["2x reached"] and p.armed
    ev = p.tick(5.5, 1650000, 80000, t0 + 180, P)                          # jumps through 3x, 5x and $1M at once
    assert sorted(e.label for e in ev) == ["5x reached", "market cap passed $1M"]
    assert p.tick(5.4, 1620000, 80000, t0 + 240, P) == []                 # nothing is announced twice
    assert p.tick(2.6, 780000, 80000, t0 + 300, P) == []                  # 53% off the peak, first poll: not yet
    ev = p.tick(2.6, 780000, 80000, t0 + 320, P)
    assert ev[-1].kind == "exit" and "trailing stop" in ev[-1].label and p.closed
    assert p.tick(9.0, 1e6, 80000, t0 + 400, P) == []


def test_position_stop_needs_two_polls_and_liquidity_pull():
    P, t0 = CFG.trade, 1000.0
    p = Position(1, "sol:x", t0, 1.0, 300000, 60000)
    assert p.tick(0.55, 165000, 60000, t0 + 60, P) == []                  # one freak print
    assert p.tick(0.9, 270000, 60000, t0 + 80, P) == [] and not p.closed
    assert p.tick(0.58, 170000, 60000, t0 + 100, P) == []
    ev = p.tick(0.57, 170000, 60000, t0 + 120, P)
    assert ev[0].kind == "exit" and ev[0].label == "stop -40%"
    p = Position(2, "sol:y", t0, 1.0, 300000, 60000)
    p.tick(1.0, 300000, 20000, t0 + 60, P)
    assert p.tick(1.0, 300000, 20000, t0 + 80, P)[0].label == "liquidity pulled"
    p = Position(3, "sol:z", t0, 1.0, 300000, 60000)
    assert p.tick(1.2, 360000, 60000, t0 + P.max_days * 86400 + 1, P)[0].label == "7 days up"
    assert Position(4, "sol:q", t0, 1.0, 1, 1).lost()[0].mult == 0.0


def test_position_safety_triggers():
    P = CFG.trade
    p = Position(1, "sol:x", 0.0, 1.0, 300000, 60000)
    p.dev_closed_at_entry = 0
    assert p.safety({"d_closed": 0}, 10, P) == []
    w = p.safety({"h_sig_bundler_sell_n": 1}, 20, P)
    assert w[0].kind == "warning" and p.safety({"h_sig_bundler_sell_n": 2}, 30, P) == []       # warned once
    ev = p.safety({"d_closed": 1}, 40, P)
    assert ev[0].kind == "exit" and ev[0].label == "dev sold out" and p.closed
    p = Position(2, "sol:y", 0.0, 1.0, 300000, 60000)
    assert "smart money exiting" in p.safety({"w_sm_sellers_1h": 3, "w_sm_net_usd_6h": -5000}, 10, P)[0].label
    p = Position(3, "sol:z", 0.0, 1.0, 300000, 60000)
    p.load_state({"sent": ["x2"], "armed": True, "dev_closed_at_entry": 1})
    assert p.armed and p.safety({"d_closed": 1}, 10, P) == []            # dev was already out at entry
