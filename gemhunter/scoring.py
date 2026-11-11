"""Pass / fail gates (spec group 1 and the hard manipulation limits) and the day-one scorecard.

The scorecard follows the priority order of GEM_HUNTER_FACTORS.md: manipulation and distribution first,
then wallet quality, order flow, cost basis, price structure, attention, liquidity. Each item maps one
factor to 0..1 (1 = what a gem looks like); a group's score is the mean of the items that have data, and
a group with no data scores neutral (0.5). Once a learned model has passed its tests it decides instead
and the scorecard stays as the benchmark it must beat.
"""
from __future__ import annotations

EVM = ("eth", "base", "bsc", "robinhood")


def gates(f: dict, g, scope, final: bool = False) -> list[str]:
    """Reasons this token may not be called (empty = passes). `final` adds what only the deep look can
    answer (a contract check must have answered)."""
    bad: list[str] = []
    chain = f.get("chain")

    def over(key: str, limit, label: str):
        v = f.get(key)
        if v is not None and limit is not None and v > limit:
            bad.append(f"{label} {v:.0f}%")
    mcap = f.get("_mcap")
    if mcap is None or not (scope.mcap_min_usd <= mcap <= scope.mcap_max_usd):
        bad.append("market cap outside range")
    if f.get("s_honeypot") == 1 or f.get("gp_honeypot") == 1:
        bad.append("honeypot")
    over("s_tax_max_pct", g.max_tax_pct, "tax")
    over("gp_tax_max_pct", g.max_tax_pct, "tax (GoPlus)")
    if chain == "sol":
        if f.get("s_mint_renounced") == 0:
            bad.append("mint authority not revoked")
        if f.get("s_freeze_renounced") == 0:
            bad.append("freeze authority not revoked")
    lp_safe = bool(f.get("s_lp_burned") or f.get("l_on_curve")
                   or max(f.get("s_lp_locked_pct") or 0, f.get("gp_lp_locked_pct") or 0) >= g.min_lp_locked_pct)
    if chain in EVM:
        if f.get("s_open_source") == 0 or f.get("gp_open_source") == 0:
            bad.append("contract not verified")
        if f.get("s_owner_renounced") == 0 and not lp_safe:
            bad.append("owner keeps control and liquidity is not locked")
        if f.get("gp_tax_modifiable") or f.get("gp_blacklist") or f.get("gp_pausable") or f.get("gp_hidden_owner") \
                or f.get("gp_owner_change_balance") or f.get("gp_take_back_owner"):
            if f.get("gp_renounced") != 1:
                bad.append("owner powers (blacklist / pause / tax change / hidden owner)")
    lp_known = any(f.get(k) is not None for k in ("s_lp_burned", "s_lp_locked_pct", "gp_lp_locked_pct", "l_on_curve"))
    if lp_known and not lp_safe:
        bad.append("liquidity not burned or locked")
    if (f.get("s_rug_ratio") or 0) > g.max_rug_ratio:
        bad.append(f"rug risk {f['s_rug_ratio']:.2f}")
    if f.get("s_wash") == 1:
        bad.append("wash trading")
    over("h_top10_pct", g.max_top10_pct, "top 10 hold")
    over("h_bundler_pct", g.max_bundler_pct, "bundlers")
    over("h_insider_pct", g.max_insider_pct, "insiders")
    over("h_entrapment_pct", g.max_entrapment_pct, "entrapment")
    over("d_team_pct", g.max_dev_team_pct, "dev team holds")
    over("d_creator_pct", g.max_creator_pct, "creator holds")
    liq = f.get("l_liq_usd")
    if liq is not None and liq < g.min_liq_usd:
        bad.append("liquidity too thin")
    if f.get("h_holders") is not None and f["h_holders"] < g.min_holders:
        bad.append("too few holders")
    if f.get("f_vol_24h") is not None and f["f_vol_24h"] < g.min_vol_24h_usd:
        bad.append("almost no trading")
    if f.get("l_liq_to_mcap") is not None and f["l_liq_to_mcap"] < g.min_liq_to_mcap:
        bad.append("liquidity tiny against market cap")
    sd = g.serial_deployer
    if (f.get("d_created_n") or 0) >= sd.min_created and (f.get("d_open_ratio") if f.get("d_open_ratio") is not None else 1) <= sd.max_open_ratio:
        bad.append(f"serial deployer ({f['d_created_n']:.0f} tokens)")
    if (f.get("own_dev_rug_n") or 0) > g.max_deployer_rugs:
        bad.append("deployer rugged an earlier call")
    if final and g.require_security and not (f.get("s_checked") or f.get("gp_ok") or f.get("s_list_check")):
        bad.append("no contract check answered")
    return bad


# ------------------------------------------------------------------------------- scorecard
def lo(v, good, bad):
    """Lower is better: 1 at `good` or below, 0 at `bad` or above."""
    return None if v is None else max(0.0, min(1.0, (bad - v) / (bad - good)))


def hi(v, bad, good):
    """Higher is better: 0 at `bad` or below, 1 at `good` or above."""
    return None if v is None else max(0.0, min(1.0, (v - bad) / (good - bad)))


def band(v, a, b, c, d):
    """Best inside b..c, fading to 0 at a and d."""
    if v is None:
        return None
    if v <= a or v >= d:
        return 0.0
    return 1.0 if b <= v <= c else (v - a) / (b - a) if v < b else (d - v) / (d - c)


def flag(v, when_1: float = 1.0, when_0: float = 0.0):
    return None if v is None else (when_1 if v else when_0)


# (group, label shown in alerts, function of the factors -> 0..1 or None)
ITEMS = [
    # groups 3 + 4: manipulation and distribution - hardest to fake, most discriminating
    ("manipulation", "few bundlers", lambda f: lo(f.get("h_bundler_pct"), 3, 30)),
    ("manipulation", "few insiders", lambda f: lo(f.get("h_insider_pct"), 3, 30)),
    ("manipulation", "no entrapment", lambda f: lo(f.get("h_entrapment_pct"), 3, 30)),
    ("manipulation", "supply spread out", lambda f: lo(f.get("h_top10_pct"), 15, 40)),
    ("manipulation", "snipers gone", lambda f: lo(f.get("h_sniper_hold_pct"), 1, 15)),
    ("manipulation", "dev out or small", lambda f: 1.0 if f.get("d_closed") == 1 else lo(f.get("d_team_pct"), 1, 10)),
    ("manipulation", "few fresh wallets", lambda f: lo(f.get("h_fresh_pct"), 10, 50)),
    ("manipulation", "no hidden supply", lambda f: lo(f.get("h_vault_pct"), 0, 10)),
    ("manipulation", "holders not one entity", lambda f: lo(f.get("h_cluster_max_pct"), 2, 20)),
    ("manipulation", "deployer not serial", lambda f: lo(f.get("d_created_n"), 2, 30)),
    ("manipulation", "deployer's coins graduate", lambda f: hi(f.get("d_open_ratio"), 0.02, 0.4)
     if (f.get("d_created_n") or 0) >= 3 else None),
    ("manipulation", "deployer's coins survive", lambda f: lo(f.get("d_prev_dead_pct"), 30, 100)),
    ("manipulation", "holders growing", lambda f: hi(f.get("h_holder_growth_1h_pct"), -1, 6)),
    ("manipulation", "supply spreading", lambda f: lo(f.get("h_top10_delta_6h"), -3, 3)),
    ("manipulation", "bundlers not selling", lambda f: lo(f.get("h_sig_bundler_sell_n"), 0, 3)),
    ("manipulation", "deployer clean with us", lambda f: flag(f.get("own_dev_win_n"), 1.0, 0.5)
     if f.get("own_dev_prev_n") else None),
    # group 6: smart money and wallet quality
    ("smart", "many smart wallets", lambda f: hi(f.get("w_smart_n"), 3, 150)),
    ("smart", "smart holders bought", lambda f: hi(f.get("w_smart_bought_n"), 0, 5)),
    ("smart", "smart holders not seeded", lambda f: lo(f.get("w_smart_seeded_n"), 0, 3)),
    ("smart", "smart wallets converging", lambda f: hi(f.get("w_sm_converge_n"), 0, 3)),
    ("smart", "smart money net buying", lambda f: None if f.get("w_sm_net_usd_6h") is None or not (
        f.get("w_sm_buyers_6h") or f.get("w_sm_sellers_6h")) else hi(f["w_sm_net_usd_6h"], -2000, 3000)),
    ("smart", "smart money not exiting", lambda f: lo(f.get("w_smart_exited_n"), 0, 4)),
    ("smart", "not late to smart money", lambda f: lo(f.get("w_smart_entry_ratio") or f.get("w_sm_entry_ratio"), 1.2, 5)),
    ("smart", "no KOL-in smart-out", lambda f: flag(f.get("w_kol_in_smart_out"), 0.0, 0.7)),
    ("smart", "blue-chip holders", lambda f: hi(f.get("w_bluechip_pct"), 10, 80) if f.get("chain") == "sol" else None),
    ("smart", "proven wallets early", lambda f: hi(f.get("own_proven_n"), 0, 3)),
    ("smart", "old wallets", lambda f: hi(f.get("w_age_median_d"), 5, 120)),
    # group 7: order flow, fees as the anchor for real demand
    ("flow", "fees paid", lambda f: hi(f.get("f_fee_to_mcap"), 0.002, 0.12)),
    ("flow", "turnover vs pool", lambda f: band(f.get("f_vol1h_to_liq"), 0.02, 0.3, 4, 15)),
    ("flow", "buyers outnumber sellers", lambda f: hi(f.get("f_buy_ratio_n_1h"), 0.42, 0.6)),
    ("flow", "money flowing in", lambda f: hi(f.get("f_buy_ratio_usd_1h"), 0.42, 0.62)),
    ("flow", "activity accelerating", lambda f: hi(f.get("f_swaps_accel_1h"), 0.5, 3)),
    ("flow", "few bots", lambda f: lo(f.get("f_bot_pct"), 10, 60)),
    ("flow", "many small buyers", lambda f: lo(f.get("f_buy_top5_share_pct"), 20, 70)),
    ("flow", "broad demand", lambda f: hi(f.get("f_buyer_seller_ratio"), 0.8, 2.5)),
    # group 5: holder cost basis and retention
    ("retention", "little supply in big profit", lambda f: lo(f.get("c_profit_5x_pct"), 2, 25)),
    ("retention", "holder base near price", lambda f: hi(f.get("c_near_price_share"), 15, 60)),
    ("retention", "early buyers still in", lambda f: hi(f.get("c_early_retained_pct"), 20, 70)),
    ("retention", "bought, not airdropped", lambda f: lo(f.get("c_transfer_in_pct"), 1, 15)),
    ("retention", "profit not yet taken", lambda f: lo(f.get("c_realized_to_mcap"), 0.05, 0.6)),
    # group 8: price structure and lifecycle
    ("structure", "survived the first dump", lambda f: flag(f.get("p_survived_dump"), 1.0, 0.4)),
    ("structure", "holding near highs", lambda f: band(f.get("p_dd_from_high_pct"), -1, 5, 45, 85)),
    ("structure", "higher lows", lambda f: hi(f.get("p_higher_lows_n"), 0, 3)),
    ("structure", "range tightening", lambda f: lo(f.get("p_range_tight"), 0.6, 1.6)),
    ("structure", "past the first hours", lambda f: hi(f.get("p_age_h"), 1, 12)),
    ("structure", "room below the high", lambda f: band(f.get("p_to_ath"), 0.1, 0.35, 0.95, 1.4)),
    ("structure", "community takeover", lambda f: flag(f.get("p_cto"), 0.8, None) if f.get("p_cto") else None),
    # groups 9 + 10: attention and context amplify a move more than they predict one
    ("attention", "has socials", lambda f: flag(f.get("a_has_social"), 0.7, 0.2)),
    ("attention", "X account not recycled", lambda f: lo(f.get("a_x_renamed_n"), 0, 3)),
    ("attention", "socials not copied", lambda f: lo(f.get("a_social_dup_n"), 0, 3)),
    ("attention", "Telegram calls", lambda f: hi(f.get("a_tg_calls"), 0, 15)),
    ("attention", "smart / KOL signals", lambda f: hi((f.get("w_sig_smart_buy_n") or 0) + (f.get("w_sig_kol_buy_n") or 0), 0, 4)
     if f.get("a_sig_n_24h") is not None else None),
    ("attention", "market not falling", lambda f: hi(f.get("mk_native_chg_24h"), -8, 4)),
    ("attention", "Telegram group is alive", lambda f: hi(f.get("a_tg_online_pct"), 0.5, 6)
     if f.get("a_tg_members") and not f.get("a_tg_is_channel") else None),
    ("attention", "Telegram community size", lambda f: hi(f.get("a_tg_members"), 100, 3000)),
    ("attention", "calls picking up", lambda f: hi(f.get("a_tg_calls_per_h"), 0, 2)),
    ("attention", "search interest rising", lambda f: hi(f.get("a_visits_per_h"), 0, 20)),
    ("attention", "X account followed", lambda f: hi(f.get("a_x_followers"), 200, 20000)),
    ("attention", "theme is trading now", lambda f: hi(f.get("m_meta_heat"), 0.02, 0.25)),
    ("attention", "theme has recent winners", lambda f: hi(f.get("m_meta_winners_n"), 0, 3)),
    ("attention", "original, not a copy", lambda f: flag(f.get("a_symbol_oldest"), 1.0, 0.2) if f.get("a_symbol_twins_n") else None),
    # group 11: fundamentals - only for tokens that claim a product
    ("fundamentals", "site on an aged domain", lambda f: hi(f.get("a_web_domain_age_d"), 14, 365) if f.get("fu_claims_product") else None),
    ("fundamentals", "site has real content", lambda f: hi(f.get("a_web_words"), 150, 1500) if f.get("fu_claims_product") else None),
    ("fundamentals", "code is active", lambda f: lo(f.get("fu_gh_pushed_d"), 7, 120) if f.get("fu_gh_repo") else None),
    ("fundamentals", "code is its own", lambda f: flag(f.get("fu_gh_fork"), 0.2, 0.9) if f.get("fu_gh_repo") else None),
    ("fundamentals", "usage on record", lambda f: hi(f.get("fu_tvl_usd"), 10000, 1000000)),
    ("fundamentals", "audit on record", lambda f: flag(f.get("fu_audits_n"), 1.0, 0.4) if f.get("fu_tvl_usd") is not None else None),
    ("fundamentals", "listed on CoinGecko", lambda f: 0.9 if f.get("fu_cg_listed") else None),
    # group 2: liquidity and market structure
    ("liquidity", "pool depth vs size", lambda f: band(f.get("l_liq_to_mcap"), 0.03, 0.08, 0.35, 0.8)),
    ("liquidity", "low slippage", lambda f: lo(f.get("l_slip_1k_pct"), 1, 8)),
]


def scorecard(f: dict, weights, tuning: dict | None = None) -> tuple[float, dict, list[str], list[str]]:
    """(score 0-100, score per group 0-1, strongest points, weakest points). `tuning` (tuning.py) carries the
    multipliers learned from graded snapshots: per item inside its group, and per group around its base weight."""
    t_items, t_groups = (tuning or {}).get("items") or {}, (tuning or {}).get("groups") or {}
    groups: dict[str, list[tuple[float, float]]] = {}
    marks: list[tuple[float, str, str]] = []
    for group, label, fn in ITEMS:
        try:
            v = fn(f)
        except (TypeError, ValueError, ZeroDivisionError):
            v = None
        if v is not None:
            groups.setdefault(group, []).append((v, float((t_items.get(label) or {}).get("mult") or 1.0)))
            marks.append((v, label, group))
    per = {g: (sum(v * w for v, w in groups[g]) / sum(w for _, w in groups[g]) if groups.get(g) else 0.5) for g in weights}
    wg = {g: float(weights[g]) * float((t_groups.get(g) or {}).get("mult") or 1.0) for g in weights}
    total = sum(wg.values()) or 1.0
    score = sum(per[g] * wg[g] for g in weights) / total * 100
    order = {g: -wg[g] for g in weights}
    good = [m[1] for m in sorted((m for m in marks if m[0] >= 0.8), key=lambda m: (order.get(m[2], 0), -m[0]))][:6]
    bad = [m[1] for m in sorted((m for m in marks if m[0] <= 0.25), key=lambda m: (order.get(m[2], 0), m[0]))][:5]
    return round(score, 1), {g: round(v, 3) for g, v in per.items()}, good, ["weak: " + b for b in bad]
