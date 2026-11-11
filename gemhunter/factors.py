"""Every factor of GEM_HUNTER_FACTORS.md, as plain numbers. Pure functions: raw source data in, factors out.

Names carry the spec's group:  s_ contract safety (1) · l_ liquidity (2) · d_ deployer (3) · h_ holders and
manipulation (4) · c_ cost basis and retention (5) · w_ smart money and wallets (6) · f_ order flow (7) ·
p_ price and lifecycle (8) · a_ attention (9) · m_ market context (10) · own_ this program's own history.
GMGN rates come as 0-1 and are stored as percent (_pct). A factor no source answered is simply absent.
Keys starting with "_" are working data for the engine, never stored as factors.
"""
from __future__ import annotations

import statistics
import time

SIGNAL_GROUPS = {"p_sig_spike_n": (1, 6), "a_sig_dex_paid_n": (2, 3, 4, 5), "p_sig_ath_n": (7,), "p_sig_keylevel_n": (8,),
                 "a_sig_live_n": (9,), "h_sig_bundler_sell_n": (10,), "p_sig_cto_n": (11,), "w_sig_smart_buy_n": (12,),
                 "a_sig_call_n": (13, 19), "m_sig_fee_claim_n": (17, 18, 21), "w_sig_kol_buy_n": (20,)}


def num(x) -> float | None:
    try:
        return None if x in (None, "") else float(x)
    except (TypeError, ValueError):
        return None


def pct(x) -> float | None:
    v = num(x)
    return None if v is None else v * 100


def yn(x) -> int | None:
    """GMGN flags come as 1 / 0, true / false or "yes" / "no" / "unknown"."""
    if x in (None, "", "unknown"):
        return None
    if isinstance(x, str):
        return 1 if x.lower() in ("yes", "true", "1") else 0 if x.lower() in ("no", "false", "0") else None
    return int(bool(x))


def put(f: dict, d: dict | None) -> dict:
    for k, v in (d or {}).items():
        if v is not None:
            f[k] = v
    return f


def div(a, b) -> float | None:
    return a / b if a is not None and b else None


def _tags(w: dict) -> set[str]:
    return {str(t) for k in ("tags", "maker_token_tags") for t in (w.get(k) or []) if t}


def _closed(status, close=None) -> int | None:
    if close is not None:
        return int(bool(close))
    return None if not status else int(str(status) in ("creator_close", "sell", "close"))


# ------------------------------------------------------------------------------- GMGN lists
def _shared(i: dict) -> dict:
    """Fields the rank list and the trenches list spell the same way."""
    tax = [pct(i.get(k)) for k in ("buy_tax", "sell_tax") if i.get(k) not in (None, "")]
    return {
        "s_mint_renounced": yn(i.get("renounced_mint")), "s_freeze_renounced": yn(i.get("renounced_freeze_account")),
        "s_honeypot": yn(i.get("is_honeypot")),
        "s_tax_max_pct": max(tax) if tax else (0.0 if "buy_tax" in i or "sell_tax" in i else None),
        "s_lp_burned": None if i.get("burn_status") is None else int(i.get("burn_status") == "burn"),
        "s_rug_ratio": num(i.get("rug_ratio")), "s_wash": yn(i.get("is_wash_trading")),
        "h_holders": num(i.get("holder_count")), "h_top10_pct": pct(i.get("top_10_holder_rate")),
        "h_insider_pct": pct(i.get("rat_trader_amount_rate")),
        "h_entrapment_pct": pct(i.get("entrapment_ratio")),
        "h_sniper_n": num(i.get("sniper_count")), "h_sniper_hold_pct": pct(i.get("top70_sniper_hold_rate")),
        "d_team_pct": pct(i.get("dev_team_hold_rate")),
        "d_closed": _closed(i.get("creator_token_status"), i.get("creator_close")),
        "w_smart_n": num(i.get("smart_degen_count")), "w_kol_n": num(i.get("renowned_count")),
        "f_bot_pct": pct(i.get("bot_degen_rate")), "f_bot_n": num(i.get("bot_degen_count")),
        "p_cto": yn(i.get("cto_flag")),
        "a_dex_paid": int(any(num(i.get(k)) for k in ("dexscr_ad", "dexscr_update_link", "dexscr_trending_bar"))),
        "a_dex_boost": num(i.get("dexscr_boost_fee")),
        "l_liq_usd": num(i.get("liquidity")),
        # the list itself answers the contract questions: mint / freeze on Solana, honeypot or verification on EVM
        "s_list_check": 1 if (i.get("renounced_mint") not in (None, "") and i.get("renounced_freeze_account") not in (None, ""))
        or any(i.get(k) not in (None, "", "unknown") for k in ("is_honeypot", "is_open_source", "open_source")) else None,
        "_creator": str(i.get("creator") or "")[:80], "_launchpad": str(i.get("launchpad_platform") or "")[:40],
        "_exchange": str(i.get("exchange") or "")[:40],
        "_created": num(i.get("creation_timestamp")) or num(i.get("created_timestamp")),
        "_opened": num(i.get("open_timestamp")),
    }


def from_rank(i: dict, interval: str) -> dict:
    """One item of /v1/market/rank. `volume`, `swaps`, `buys`, `sells` are for the interval asked."""
    w = interval
    f = _shared(i)
    put(f, {
        "_price": num(i.get("price")), "_mcap": num(i.get("market_cap")),
        "s_open_source": yn(i.get("is_open_source")), "s_owner_renounced": yn(i.get("is_renounced")),
        "s_lp_locked_pct": pct(i.get("lock_percent")),
        "h_bundler_pct": pct(i.get("bundler_rate")),
        "d_burn_pct": pct(i.get("dev_token_burn_ratio")),
        "w_bluechip_pct": min(pct(i.get("bluechip_owner_percentage")) or 0.0, 100.0)
        if i.get("bluechip_owner_percentage") not in (None, "") else None,
        f"g_vol_{w}": num(i.get("volume")), f"g_swaps_{w}": num(i.get("swaps")),
        f"g_buys_{w}": num(i.get("buys")), f"g_sells_{w}": num(i.get("sells")),
        "f_gas_fee": num(i.get("gas_fee")),
        "f_rank": num(i.get("rank")), "f_hot": num(i.get("hot_level")),
        "p_ath_mcap": num(i.get("history_highest_market_cap")), "l_initial_liq": num(i.get("initial_liquidity")),
        "p_chg_1m": pct(i.get("price_change_percent1m")), "p_chg_5m": pct(i.get("price_change_percent5m")),
        "p_chg_1h": pct(i.get("price_change_percent1h")),
        "a_has_x": int(bool(i.get("twitter_username"))), "a_has_tg": int(bool(i.get("telegram"))),
        "a_has_web": int(bool(i.get("website"))),
        "_supply": num(i.get("total_supply")),
        "_x": str(i.get("twitter_username") or "")[:40],
        "_tg": str(i.get("telegram") or "")[:120] or None, "_web": str(i.get("website") or "")[:200] or None,
        "_logo": bool(i.get("logo")) if "logo" in i else None,
    })
    return f


def from_trench(i: dict) -> dict:
    """One item of /v1/trenches "completed": the creator and wallet-label figures the rank list lacks."""
    f = _shared(i)
    created, opened = num(i.get("creator_created_count")), num(i.get("creator_created_open_count"))
    put(f, {
        "_mcap": num(i.get("market_cap")) or num(i.get("usd_market_cap")), "_price": num(i.get("price")),
        "s_open_source": yn(i.get("open_source")), "s_owner_renounced": yn(i.get("owner_renounced")),
        "h_bundler_pct": pct(i.get("bundler_trader_amount_rate")),
        "h_insider_hold_pct": pct(i.get("suspected_insider_hold_rate")),
        "h_fresh_pct": pct(i.get("fresh_wallet_rate")), "h_vault_pct": pct(i.get("private_vault_hold_rate")),
        "d_creator_pct": pct(i.get("creator_balance_rate")),
        "d_created_n": created, "d_open_n": opened,
        "d_open_ratio": num(i.get("creator_created_open_ratio")) if i.get("creator_created_open_ratio") not in (None, "")
        else div(opened, created),
        "d_fund_age_h": (time.time() - num(i["fund_from_ts"])) / 3600 if num(i.get("fund_from_ts")) else None,
        "d_x_deleted_n": num(i.get("twitter_del_post_token_count")),
        "d_x_tokens_n": num(i.get("twitter_create_token_count")),
        "a_callouts": num(i.get("callout_count")), "p_og": yn(i.get("is_og")),
        "d_burn_pct": pct(i.get("dev_token_burn_ratio")),
        "g_vol_1h": num(i.get("volume_1h")), "g_vol_24h": num(i.get("volume_24h")),
        "g_swaps_1m": num(i.get("swaps_1m")), "g_swaps_1h": num(i.get("swaps_1h")), "g_swaps_24h": num(i.get("swaps_24h")),
        "g_buys_24h": num(i.get("buys_24h")), "g_sells_24h": num(i.get("sells_24h")),
        "f_net_buy_24h": num(i.get("net_buy_24h")), "f_total_fee": num(i.get("total_fee")),
        "l_migrate_s": num(i.get("complete_cost_time")),
        "a_x_followers": num(i.get("x_user_follower")), "a_tg_calls": num(i.get("tg_call_count")),
        "a_x_following": num(i.get("x_user_following")),
        "a_x_follow_ratio": div(num(i.get("x_user_follower")), max(num(i.get("x_user_following")) or 0.0, 1.0))
        if num(i.get("x_user_follower")) is not None else None,
        "a_x_is_post": yn(i.get("twitter_is_tweet")),          # the "X account" is one post, not the coin's own account
        "a_x_community": int(bool(i.get("twitter_community_id"))) if "twitter_community_id" in i else None,
        "_tg": str(i.get("telegram") or "")[:120] or None, "_web": str(i.get("website") or "")[:200] or None,
        "_logo": bool(i.get("logo")) if "logo" in i else None,
        "a_x_renamed_n": num(i.get("twitter_rename_count")),
        "a_social_dup_n": max((num(i.get(k)) or 0 for k in ("twitter_dup", "telegram_dup", "website_dup")), default=0)
        if any(k in i for k in ("twitter_dup", "telegram_dup", "website_dup")) else None,
        "a_image_dup_n": num(i.get("image_dup")),
        "a_visits": num(i.get("visiting_count")),
        "a_has_x": int(bool(i.get("twitter"))) if "twitter" in i else None,
        "a_has_tg": int(bool(i.get("telegram"))) if "telegram" in i else None,
        "a_has_web": int(bool(i.get("website"))) if "website" in i else None,
        "a_has_social": yn(i.get("has_at_least_one_social")),
    })
    return f


# ------------------------------------------------------------------------------- DexScreener
def from_dex(d: dict) -> dict:
    f = {"_price": d.get("price"), "_mcap": d.get("mcap"), "l_liq_usd": d.get("liq"), "l_pools_n": d.get("pools_n"),
         "l_pool_age_h": (time.time() - d["pool_created"]) / 3600 if d.get("pool_created") else None,
         "l_fdv_to_mcap": div(d.get("fdv"), d.get("mcap")),
         "a_dex_boost_active": d.get("boosts"), "_dex": d.get("dex"), "_pool": d.get("pool")}
    for w in ("5m", "1h", "6h", "24h"):
        put(f, {f"f_vol_{w}": d.get(f"vol_{w}"), f"f_buys_{w}": d.get(f"buys_{w}"), f"f_sells_{w}": d.get(f"sells_{w}"),
                f"p_chg_{w}": d.get(f"chg_{w}")})
    return f


# ------------------------------------------------------------------------------- GMGN token page
def from_info(info: dict, address: str = "") -> dict:
    st, tags, dev = info.get("stat") or {}, info.get("wallet_tags_stat") or {}, info.get("dev") or {}
    link, price, pool = info.get("link") or {}, info.get("price") or {}, info.get("pool") or {}
    if not isinstance(price, dict):
        price = {"price": price}
    px, circ, total = num(price.get("price")), num(info.get("circulating_supply")), num(info.get("total_supply"))
    status, progress = num(info.get("launchpad_status")), num(info.get("launchpad_progress"))
    ath = dev.get("ath_token_info") if isinstance(dev.get("ath_token_info"), dict) else {}
    best_other = num(ath.get("ath_mc")) if ath.get("ath_token") and str(ath.get("ath_token")) != str(address) else None
    renames = dev.get("twitter_name_change_history")
    fee = info.get("fee_distribution") or {}
    fee_list = ((fee.get("platform_data") or {}).get("list") or []) if isinstance(fee, dict) else []
    f = {
        "_price": px, "_mcap": px * circ if px and circ else None, "_supply": total,
        "_creator": str(dev.get("creator_address") or "")[:80],
        "_launchpad": str(info.get("launchpad_platform") or info.get("launchpad") or "")[:40],
        "_created": num(info.get("creation_timestamp")), "_opened": num(info.get("open_timestamp")),
        "_desc": str(link.get("description") or "")[:300],
        "l_liq_usd": num(info.get("liquidity")), "l_fdv_to_mcap": div(total, circ),
        "l_on_curve": int(status in (0, 1) and progress is not None and 0 < progress < 1) if status is not None else None,
        "l_migration_mcap": num(info.get("migration_market_cap")),
        "l_pool_fee_pct": pct(pool.get("fee_ratio")),
        "l_quote_reserve_usd": num(pool.get("quote_reserve_value")),
        "s_lp_locked_pct": pct(info.get("locked_ratio")),
        "p_ath_price": num(info.get("ath_price")), "p_og": yn(info.get("og")),
        "h_holders": num(st.get("holder_count") or info.get("holder_count")),
        "h_top10_pct": pct(st.get("top_10_holder_rate")),
        "h_insider_top_pct": pct(st.get("top_rat_trader_percentage")),
        "h_bundler_top_pct": pct(st.get("top_bundler_trader_percentage")),
        "h_entrapment_top_pct": pct(st.get("top_entrapment_trader_percentage")),
        "h_fresh_pct": pct(st.get("fresh_wallet_rate")), "h_vault_pct": pct(st.get("private_vault_hold_rate")),
        "f_bot_pct": pct(st.get("bot_degen_rate")), "f_bot_n": num(st.get("bot_degen_count")),
        "d_team_pct": pct(st.get("dev_team_hold_rate")), "d_creator_pct": pct(st.get("creator_hold_rate")),
        "d_closed": _closed(dev.get("creator_token_status")),
        "d_open_n": num(dev.get("creator_open_count")),
        "d_fund_age_h": (time.time() - num(dev["fund_from_ts"])) / 3600 if num(dev.get("fund_from_ts")) else None,
        "d_x_tokens_n": num(dev.get("twitter_create_token_count")),
        "d_x_deleted_n": num(dev.get("twitter_del_post_token_count")),
        "d_best_ath_mcap": best_other,
        "d_fee_recipients_n": len(fee_list) if fee_list else None,
        "d_fee_creator_bps": sum(num(x.get("royalty_bps")) or 0 for x in fee_list if x.get("is_creator")) if fee_list else None,
        "p_cto": yn(dev.get("cto_flag")),
        "a_dex_paid": int(any(num(dev.get(k)) for k in ("dexscr_ad", "dexscr_update_link", "dexscr_trending_bar"))),
        "a_dex_boost": num(dev.get("dexscr_boost_fee")),
        "a_x_renamed_n": len(renames) if isinstance(renames, list) else None,
        "a_has_x": int(bool(link.get("twitter_username"))), "a_has_tg": int(bool(link.get("telegram"))),
        "a_has_web": int(bool(link.get("website"))), "a_verified": yn(link.get("verify_status")),
        "w_smart_n": num(tags.get("smart_wallets")), "w_kol_n": num(tags.get("renowned_wallets")),
        "h_sniper_wallets_n": num(tags.get("sniper_wallets")), "h_insider_wallets_n": num(tags.get("rat_trader_wallets")),
        "h_bundler_wallets_n": num(tags.get("bundler_wallets")), "h_fresh_wallets_n": num(tags.get("fresh_wallets")),
        "h_whale_n": num(tags.get("whale_wallets")),
        "f_hot": num(price.get("hot_level")),
        "_x": str(link.get("twitter_username") or "")[:40], "_web": str(link.get("website") or "")[:200] or None,
        "_tg": str(link.get("telegram") or "")[:120] or None, "_logo": bool(info.get("logo")) if "logo" in info else None,
    }
    for w in ("1m", "5m", "1h", "6h", "24h"):
        put(f, {f"g_vol_{w}": num(price.get(f"volume_{w}")), f"g_swaps_{w}": num(price.get(f"swaps_{w}")),
                f"g_buys_{w}": num(price.get(f"buys_{w}")), f"g_sells_{w}": num(price.get(f"sells_{w}")),
                f"f_buy_usd_{w}": num(price.get(f"buy_volume_{w}")), f"f_sell_usd_{w}": num(price.get(f"sell_volume_{w}"))})
    return f


def from_security(sec: dict) -> dict:
    tax = [pct(sec.get(k)) for k in ("buy_tax", "sell_tax") if sec.get(k) not in (None, "")]
    lock = sec.get("lock_summary") or {}
    dead = sum(num(d.get("percent")) or 0 for d in lock.get("lock_detail") or []
               if isinstance(d, dict) and d.get("is_blackhole") in (True, "true", 1, "1"))
    locked = [v for v in (pct(sec.get("lock_percent")), pct(sec.get("burn_ratio")), dead * 100 if dead else None,
                          pct(lock.get("lock_percent")) if isinstance(lock, dict) else None) if v is not None]
    return {
        "s_checked": 1,
        "s_honeypot": yn(sec.get("is_honeypot")) if sec.get("is_honeypot") not in (None, "") else yn(sec.get("honeypot")),
        "s_tax_max_pct": max(tax) if tax else 0.0,
        "s_open_source": yn(sec.get("open_source")) if "open_source" in sec else yn(sec.get("is_open_source")),
        "s_owner_renounced": yn(sec.get("owner_renounced")) if "owner_renounced" in sec else yn(sec.get("is_renounced")),
        "s_mint_renounced": yn(sec.get("renounced_mint")), "s_freeze_renounced": yn(sec.get("renounced_freeze_account")),
        "s_blacklist": yn(sec.get("is_blacklist")),
        "s_lp_burned": None if sec.get("burn_status") is None else int(sec.get("burn_status") == "burn"),
        "s_lp_locked_pct": max(locked) if locked else None,
        "s_rug_ratio": num(sec.get("rug_ratio")), "s_wash": yn(sec.get("is_wash_trading")),
        "h_top10_pct": pct(sec.get("top_10_holder_rate")), "d_team_pct": pct(sec.get("dev_team_hold_rate")),
        "d_creator_pct": pct(sec.get("creator_balance_rate")),
        "d_closed": _closed(sec.get("creator_token_status")),
        "h_insider_hold_pct": pct(sec.get("suspected_insider_hold_rate")),
        "h_insider_pct": pct(sec.get("rat_trader_amount_rate")),
        "h_bundler_pct": pct(sec.get("bundler_trader_amount_rate")),
        "h_sniper_n": num(sec.get("sniper_count")),
    }


# ------------------------------------------------------------------------------- holders and traders
def _wallets(rows: list[dict]) -> list[dict]:
    """Real wallets only: pools, exchanges and curve accounts are not holders."""
    return [w for w in rows or [] if w.get("address") and num(w.get("addr_type")) != 2 and not w.get("exchange")
            and not w.get("is_on_curve")]


def from_holders(rows: list[dict], price: float | None, now: float | None = None) -> dict:
    """Top holders (GMGN, up to 100 by share of supply): concentration, clusters, cost basis, smart money."""
    now = now or time.time()
    ws = _wallets(rows)
    if not ws:
        return {}
    share = lambda w: (num(w.get("amount_percentage")) or 0) * 100        # noqa: E731
    shares = sorted((share(w) for w in ws), reverse=True)
    f: dict = {"h_list_n": len(ws), "h_top1_pct": shares[0], "h_top10_list_pct": sum(shares[:10]),
               "h_top20_pct": sum(shares[:20])}
    # one entity behind many wallets: holders funded from the same source
    groups: dict[str, list[float]] = {}
    for w in ws:
        src = (w.get("native_transfer") or {}).get("address") if isinstance(w.get("native_transfer"), dict) else None
        if src:
            groups.setdefault(str(src), []).append(share(w))
    multi = [g for g in groups.values() if len(g) >= 2]
    f["h_cluster_max_pct"] = max((sum(g) for g in multi), default=0.0)
    f["h_cluster_wallets_n"] = sum(len(g) for g in multi)
    f["h_suspicious_pct"] = sum(share(w) for w in ws if w.get("is_suspicious"))
    f["h_new_wallet_pct"] = sum(share(w) for w in ws if w.get("is_new"))
    # cost basis: who sits on what profit
    held = sum(shares) or 1.0
    if price:
        ratio = [(price / c, share(w)) for w in ws if (c := num(w.get("avg_cost"))) and c > 0]
        if ratio:
            f["c_profit_5x_pct"] = sum(s for r, s in ratio if r >= 5)
            f["c_profit_2x_pct"] = sum(s for r, s in ratio if r >= 2)
            f["c_near_price_pct"] = sum(s for r, s in ratio if 0.7 <= r <= 1.4)
            f["c_underwater_pct"] = sum(s for r, s in ratio if r < 0.7)
            f["c_near_price_share"] = f["c_near_price_pct"] / held * 100
    bought = lambda w: (num(w.get("buy_tx_count_cur")) or 0) > 0          # noqa: E731
    seeded = [w for w in ws if w.get("transfer_in") or (not bought(w) and (num(w.get("history_transfer_in_amount")) or 0) > 0)]
    f["c_transfer_in_pct"] = sum(share(w) for w in seeded)
    f["c_paper_hands_n"] = sum(1 for w in ws if "paper_hands" in _tags(w))
    f["c_diamond_hands_n"] = sum(1 for w in ws if "diamond_hands" in _tags(w))
    # whales: net flow of the 20 largest (GMGN: negative netflow_usd = net spent, i.e. buying)
    top20 = sorted(ws, key=share, reverse=True)[:20]
    flows = [num(w.get("netflow_usd")) for w in top20 if num(w.get("netflow_usd")) is not None]
    if flows:
        f["h_whale_net_buy_usd"] = -sum(flows)
        f["h_whale_adding_n"] = sum(1 for x in flows if x < 0)
    # smart money: counted only when it bought (devs send tokens to known wallets to fake this)
    smart = [w for w in ws if "smart_degen" in _tags(w)]
    real = [w for w in smart if bought(w)]
    f["w_smart_bought_n"] = len(real)
    f["w_smart_seeded_n"] = len(smart) - len(real)
    f["w_smart_supply_pct"] = sum(share(w) for w in real)
    cost = [(num(w.get("avg_cost")), num(w.get("buy_volume_cur")) or 1.0) for w in real if num(w.get("avg_cost"))]
    if cost and price:
        f["w_smart_entry_ratio"] = price / (sum(c * v for c, v in cost) / sum(v for _, v in cost))
    f["w_smart_exited_n"] = sum(1 for w in smart if (num(w.get("sell_amount_percentage")) or 0) >= 0.9 or w.get("end_holding_at"))
    f["w_smart_net_buy_usd"] = -sum(num(w.get("netflow_usd")) or 0 for w in real)
    f["w_kol_holders_n"] = sum(1 for w in ws if _tags(w) & {"renowned", "kol"})
    f["w_bluechip_holders_pct"] = sum(1 for w in ws if "bluechip_owner" in _tags(w)) / len(ws) * 100
    ages = [(now - c) / 86400 for w in ws if (c := num(w.get("created_at"))) and c > 0]
    if len(ages) >= 5:
        f["w_age_median_d"] = statistics.median(ages)
        f["w_age_young_pct"] = sum(1 for a in ages if a < 7) / len(ages) * 100
    devs = [w for w in ws if _tags(w) & {"dev", "creator", "dev_team"}]
    if devs:
        f["d_list_hold_pct"] = sum(share(w) for w in devs)
        f["d_sold_pct"] = max((num(w.get("sell_amount_percentage")) or 0) for w in devs) * 100
    return f


def from_traders(rows: list[dict], mcap: float | None) -> dict:
    """Top traders (GMGN, by buy volume; includes wallets that already left): breadth, sizes, profit taken."""
    ws = _wallets(rows)
    if not ws:
        return {}
    buys = sorted(((num(w.get("buy_volume_cur")) or 0) for w in ws if (num(w.get("buy_tx_count_cur")) or 0) > 0), reverse=True)
    sellers = sum(1 for w in ws if (num(w.get("sell_tx_count_cur")) or 0) > 0)
    f: dict = {"f_traders_n": len(ws), "f_buyers_n": len(buys), "f_sellers_n": sellers,
               "f_buyer_seller_ratio": len(buys) / max(sellers, 1)}
    if buys and sum(buys) > 0:
        f["f_buy_median_usd"] = statistics.median(buys)
        f["f_buy_top5_share_pct"] = sum(buys[:5]) / sum(buys) * 100
    realized = sum(max(num(w.get("realized_profit")) or 0, 0) for w in ws)
    f["c_realized_usd"] = realized
    f["c_realized_to_mcap"] = div(realized, mcap)
    f["c_exited_pct"] = sum(1 for w in ws if (num(w.get("sell_amount_percentage")) or 0) >= 0.9) / len(ws) * 100
    f["f_bot_traders_pct"] = sum(1 for w in ws if _tags(w) & {"dex_bot", "sandwich_bot", "bot"}) / len(ws) * 100
    # early-buyer retention: of the earliest buyers we can see, how many still hold most of their bag
    early = sorted((w for w in ws if num(w.get("start_holding_at")) and (num(w.get("buy_tx_count_cur")) or 0) > 0),
                   key=lambda w: num(w.get("start_holding_at")))[:30]
    if len(early) >= 8:
        f["c_early_retained_pct"] = sum(1 for w in early if (num(w.get("sell_amount_percentage")) or 0) < 0.5) / len(early) * 100
    return f


def early_buyers(holders: list[dict] | None, traders: list[dict] | None, n: int) -> list[tuple[str, float, float]]:
    """(wallet, first buy time, usd bought) of the earliest real buyers seen: the own proven-wallet list is built
    from who was early in tokens that later ran."""
    seen: dict[str, tuple[str, float, float]] = {}
    for w in _wallets(holders or []) + _wallets(traders or []):
        t = num(w.get("start_holding_at"))
        if t and (num(w.get("buy_tx_count_cur")) or 0) > 0 and not (_tags(w) & {"dex_bot", "sandwich_bot", "bot"}):
            a = str(w["address"])
            if a not in seen or t < seen[a][1]:
                seen[a] = (a, t, num(w.get("buy_volume_cur")) or 0.0)
    return sorted(seen.values(), key=lambda x: x[1])[:n]


# ------------------------------------------------------------------------------- candles
def from_kline(candles: list[dict], price: float | None, created_ts: float | None, days: float) -> dict:
    """Price structure from GMGN candles (oldest first, at most 100 per request)."""
    def usd(c: dict) -> float:
        # GMGN's docs call `amount` the USD figure; live answers (2026-11-11) carry USD in `volume` and token
        # units in `amount`. Whichever of the two equals the other times the price is the USD one.
        v, a, px = num(c.get("volume")) or 0.0, num(c.get("amount")) or 0.0, num(c.get("close")) or 0.0
        return v if a and px and abs(a * px - v) <= abs(v * px - a) else a
    cs = [(num(c.get("time")), num(c.get("open")), num(c.get("high")), num(c.get("low")), num(c.get("close")), usd(c))
          for c in candles or []]
    cs = sorted((c for c in cs if None not in c[:5] and c[4] > 0), key=lambda c: c[0])
    if len(cs) < 6:
        return {"p_candles_n": len(cs)}
    if cs[0][0] > 1e11:                                   # milliseconds
        cs = [(c[0] / 1000, *c[1:]) for c in cs]
    price = price or cs[-1][4]
    highs, lows, closes = [c[2] for c in cs], [c[3] for c in cs], [c[4] for c in cs]
    hi, lo = max(highs), min(lows)
    i_hi = highs.index(hi)
    f: dict = {"p_candles_n": len(cs), "p_dd_from_high_pct": max(0.0, (1 - price / hi) * 100),
               "p_from_low_mult": price / lo if lo > 0 else None,
               "p_high_age_h": (cs[-1][0] - cs[i_hi][0]) / 3600}
    if created_ts and cs[0][0] - created_ts < 2 * 3600 and cs[0][1] > 0:
        f["p_mult_from_launch"] = price / cs[0][1]        # the window reaches back to the launch
    # the deepest fall from a high inside the window, and how much of it has been won back
    peak, max_dd, trough_after = cs[0][2], 0.0, None
    for _, _, h, l, _, _ in cs:
        if h > peak:
            peak = h
        dd = 1 - l / peak
        if dd > max_dd:
            max_dd, trough_after = dd, l
    f["p_max_dd_pct"] = max_dd * 100
    if trough_after:
        f["p_recovery_mult"] = price / trough_after
    f["p_survived_dump"] = int(max_dd >= 0.6 and price >= 0.5 * hi)
    # base quality: higher lows over the last four 6-candle blocks, and a range that tightens
    blocks = [lows[max(0, len(lows) - 6 * (k + 1)):len(lows) - 6 * k] for k in range(3, -1, -1)]
    mins = [min(b) for b in blocks if b]
    f["p_higher_lows_n"] = sum(1 for a, b in zip(mins, mins[1:]) if b > a)
    rng = [(c[2] - c[3]) / c[4] for c in cs]
    if len(rng) >= 24:
        recent, before = sum(rng[-6:]) / 6, sum(rng[-24:-6]) / 18
        f["p_range_tight"] = recent / before if before > 0 else None
    if len(cs) >= 12:
        a, b = sum(c[5] for c in cs[-6:]), sum(c[5] for c in cs[-12:-6])
        f["p_vol_trend"] = a / b if b > 0 else None
    if len(cs) >= 30:                                      # renewed activity after a quiet period
        quiet = statistics.median(c[5] for c in cs[:-6]) or 0.0
        f["p_reawaken"] = (sum(c[5] for c in cs[-6:]) / 6) / quiet if quiet > 0 else None
    f["p_trend_24"] = closes[-1] / closes[-min(24, len(closes))] - 1
    return f


# ------------------------------------------------------------------------------- deployer history
def from_created(d: dict, address: str, now: float | None = None) -> dict:
    """GMGN's record of every token this wallet created (the strongest rug marker is a serial deployer)."""
    now = now or time.time()
    inner, opened = num(d.get("inner_count")) or 0, num(d.get("open_count")) or 0
    toks = [t for t in d.get("tokens") or [] if isinstance(t, dict) and str(t.get("token_address")) != str(address)]
    f: dict = {"d_created_n": inner + opened, "d_open_n": opened,
               "d_open_ratio": num(d.get("open_ratio")) if d.get("open_ratio") not in (None, "") else div(opened, inner + opened)}
    ath = d.get("creator_ath_info") if isinstance(d.get("creator_ath_info"), dict) else {}
    if ath.get("ath_token") and str(ath.get("ath_token")) != str(address):
        f["d_best_ath_mcap"] = num(ath.get("ath_mc"))
    if toks:
        aths = [num(t.get("token_ath_mc")) or 0 for t in toks]
        mcs = [num(t.get("market_cap")) or 0 for t in toks]
        f["d_prev_n"] = len(toks)
        f["d_prev_median_ath"] = statistics.median(aths)
        f["d_prev_best_ath"] = max(aths)
        f["d_prev_dead_pct"] = sum(1 for m in mcs if m < 10_000) / len(toks) * 100
        f["d_prev_alive_n"] = sum(1 for m in mcs if m >= 50_000)
        br = [num(t.get("bundler_rate")) for t in toks if num(t.get("bundler_rate")) is not None]
        if br:
            f["d_prev_bundler_pct"] = sum(br) / len(br) * 100
        ts = [num(t.get("create_timestamp")) for t in toks if num(t.get("create_timestamp"))]
        if ts:
            f["d_last_create_h"] = (now - max(ts)) / 3600
            f["d_created_7d_n"] = sum(1 for t in ts if now - t < 7 * 86400)
    return f


# ------------------------------------------------------------------------------- signals and smart-money feed
def from_signals(rows: list, now: float) -> dict:
    """GMGN signal events for this token in the last 24 hours. rows: (type, trigger_at)."""
    recent = [(int(t), ts) for t, ts in rows if ts and now - ts <= 86400]
    f = {"a_sig_n_24h": len(recent)}
    for name, types in SIGNAL_GROUPS.items():
        f[name] = sum(1 for t, _ in recent if t in types)
    return f


def from_feed(trades: list, now: float, price: float | None) -> dict:
    """Smart-money and KOL trades in this token over the last 6 hours.
    trades: (kind, maker, side, usd, price, closing, ts)."""
    tr = [t for t in trades if t[6] and now - t[6] <= 6 * 3600]
    sm_buy = [t for t in tr if t[0] == "smart" and t[2] == "buy"]
    sm_sell = [t for t in tr if t[0] == "smart" and t[2] == "sell"]
    kol_buy = [t for t in tr if t[0] == "kol" and t[2] == "buy"]
    f: dict = {"w_sm_buyers_6h": len({t[1] for t in sm_buy}), "w_sm_sellers_6h": len({t[1] for t in sm_sell}),
               "w_sm_sellers_1h": len({t[1] for t in sm_sell if now - t[6] <= 3600}),
               "w_sm_buy_usd_6h": sum(t[3] or 0 for t in sm_buy), "w_sm_sell_usd_6h": sum(t[3] or 0 for t in sm_sell),
               "w_sm_closes_6h": sum(1 for t in sm_sell if t[5]),
               "w_kol_buyers_6h": len({t[1] for t in kol_buy})}
    f["w_sm_net_usd_6h"] = f["w_sm_buy_usd_6h"] - f["w_sm_sell_usd_6h"]
    # convergence: the most distinct smart wallets buying inside any 30 minutes
    best, buys = 0, sorted(sm_buy, key=lambda t: t[6])
    for i, t in enumerate(buys):
        best = max(best, len({u[1] for u in buys[i:] if u[6] - t[6] <= 1800}))
    f["w_sm_converge_n"] = best
    f["w_kol_in_smart_out"] = int(bool(kol_buy) and f["w_sm_net_usd_6h"] < 0)
    px = [(t[4], t[3] or 1.0) for t in sm_buy if t[4]]
    if px and price:
        f["w_sm_entry_ratio"] = price / (sum(p * v for p, v in px) / sum(v for _, v in px))
    return f


def from_history(obs: list) -> dict:
    """What changed while this program watched. obs: (ts, holders, top10 %, mcap), oldest first."""
    if len(obs) < 2:
        return {}
    now_ts, holders, top10 = obs[-1][0], obs[-1][1], obs[-1][2]
    f: dict = {"p_watched_h": (now_ts - obs[0][0]) / 3600}

    def back(hours: float):
        cut = now_ts - hours * 3600
        older = [o for o in obs if o[0] <= cut + 300]
        return older[-1] if older and now_ts - older[-1][0] <= hours * 3600 * 2 else None
    for hours, tag in ((1, "1h"), (6, "6h")):
        o = back(hours)
        if o and o[1] and holders:
            f[f"h_holder_growth_{tag}_pct"] = (holders / o[1] - 1) * 100 / ((now_ts - o[0]) / 3600 / hours)
        if o and o[2] is not None and top10 is not None and hours == 6:
            f["h_top10_delta_6h"] = top10 - o[2]             # negative = supply spreading out
    a, b = back(1), back(2)
    if a and b and a[1] and b[1] and holders and b[1] != a[1]:
        f["h_holder_accel"] = (holders - a[1]) / max(abs(a[1] - b[1]), 1.0)
    # attention velocity: how fast Telegram calls, call-outs, page visits and X followers are growing per hour,
    # measured between this program's own sightings (the "mention velocity" a paid feed would sell)
    for i, name in ((4, "a_tg_calls_per_h"), (5, "a_callouts_per_h"), (6, "a_visits_per_h"), (7, "a_x_followers_per_h")):
        seen = [(o[0], o[i]) for o in obs if len(o) > i and o[i] is not None and now_ts - o[0] <= 6 * 3600]
        if len(seen) >= 2 and seen[-1][0] - seen[0][0] >= 600:
            f[name] = (seen[-1][1] - seen[0][1]) / ((seen[-1][0] - seen[0][0]) / 3600)
    return f


# ------------------------------------------------------------------------------- derived
def derive(f: dict, now: float | None = None) -> dict:
    """Ratios across sources. DexScreener's flow (f_*) is preferred; GMGN's (g_*) fills what it lacks."""
    now = now or time.time()
    for w in ("5m", "1h", "6h", "24h"):
        for kind in ("vol", "buys", "sells"):
            if f.get(f"f_{kind}_{w}") is None and f.get(f"g_{kind}_{w}") is not None:
                f[f"f_{kind}_{w}"] = f[f"g_{kind}_{w}"]
    mcap, liq = f.get("_mcap"), f.get("l_liq_usd")
    put(f, {"l_liq_to_mcap": div(liq, mcap), "f_vol1h_to_liq": div(f.get("f_vol_1h"), liq),
            "f_vol24h_to_liq": div(f.get("f_vol_24h"), liq), "f_vol24h_to_mcap": div(f.get("f_vol_24h"), mcap),
            "f_vol1h_to_mcap": div(f.get("f_vol_1h"), mcap)})
    for w in ("5m", "1h", "24h"):
        b, s = f.get(f"f_buys_{w}"), f.get(f"f_sells_{w}")
        if b is not None and s is not None and b + s > 0:
            f[f"f_buy_ratio_n_{w}"] = b / (b + s)
        bu, su = f.get(f"f_buy_usd_{w}"), f.get(f"f_sell_usd_{w}")
        if bu is not None and su is not None and bu + su > 0:
            f[f"f_buy_ratio_usd_{w}"] = bu / (bu + su)
            f[f"f_net_usd_{w}"] = bu - su
            if f.get(f"f_buy_ratio_n_{w}") is not None:
                f[f"f_ratio_gap_{w}"] = f[f"f_buy_ratio_usd_{w}"] - f[f"f_buy_ratio_n_{w}"]
    if f.get("f_net_usd_1h") is not None and f.get("f_net_usd_24h") is not None:
        f["f_net_accel"] = f["f_net_usd_1h"] * 24 - f["f_net_usd_24h"]          # 1h run-rate against the day's
    n5 = (f.get("f_buys_5m") or 0) + (f.get("f_sells_5m") or 0)
    n1 = (f.get("f_buys_1h") or 0) + (f.get("f_sells_1h") or 0)
    n24 = (f.get("f_buys_24h") or 0) + (f.get("f_sells_24h") or 0)
    put(f, {"f_swaps_accel_5m": div(n5 * 12, n1) if n1 else None, "f_swaps_accel_1h": div(n1 * 24, n24) if n24 else None,
            "f_vol_accel_1h": div((f.get("f_vol_1h") or 0) * 24, f.get("f_vol_24h")) if f.get("f_vol_24h") else None})
    if liq:
        depth = f.get("l_quote_reserve_usd") or liq / 2          # constant product: impact = size / (reserve + size)
        fee = f.get("l_pool_fee_pct") or 0.0
        for size, tag in ((500, "500"), (1000, "1k"), (5000, "5k")):
            f[f"l_slip_{tag}_pct"] = size / (depth + size) * 100 + fee
    fee = f.get("f_total_fee") if f.get("f_total_fee") is not None else f.get("f_gas_fee")   # in the chain's native coin
    put(f, {"l_liq_vs_initial": div(liq, f.get("l_initial_liq")),
            "f_fee_usd": fee * f["_native_usd"] if fee is not None and f.get("_native_usd") else None,
            "f_fee_to_mcap": div(fee * f["_native_usd"], mcap) if fee is not None and f.get("_native_usd") else None,
            "p_to_ath": div(mcap, f.get("p_ath_mcap")) if f.get("p_ath_mcap") else
            div(f.get("_price"), f.get("p_ath_price")),
            "a_boost_to_mcap": div(f.get("a_dex_boost"), mcap)})
    if f.get("p_to_ath") is not None:
        f["p_to_ath"] = min(f["p_to_ath"], 1.5)
    if f.get("_created"):
        f["p_age_h"] = max(0.0, (now - f["_created"]) / 3600)
    if f.get("_opened"):
        f["p_open_age_h"] = max(0.0, (now - f["_opened"]) / 3600)
    if f.get("a_has_social") is None and any(f.get(k) is not None for k in ("a_has_x", "a_has_tg", "a_has_web")):
        f["a_has_social"] = int(bool(f.get("a_has_x") or f.get("a_has_tg") or f.get("a_has_web")))
    t = time.gmtime(now)
    f["m_hour_utc"], f["m_dow"] = t.tm_hour, t.tm_wday
    return f


def stored(f: dict) -> dict:
    """What goes into a snapshot: numbers and the few category labels, no working data."""
    return {k: v for k, v in f.items() if not k.startswith(("_", "g_")) and v is not None
            and (isinstance(v, (int, float)) or k in ("chain", "launchpad", "stage", "narrative"))}
