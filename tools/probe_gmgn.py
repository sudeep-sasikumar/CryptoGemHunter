r"""Check every GMGN route this program uses against a live answer with your key, and show which of the fields
the factor list relies on actually come back. Run it once after setting GMGN_API_KEY in .env:

    .venv\Scripts\python.exe tools\probe_gmgn.py              all five chains (about 2 minutes at 1 weight/s)
    .venv\Scripts\python.exe tools\probe_gmgn.py --chain sol  one chain

Field names in gemhunter/factors.py come from GMGN's public documentation; this is how they are verified.
Prints field names and counts only - never the key.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from gemhunter import factors as F  # noqa: E402
from gemhunter.config import load_config  # noqa: E402
from gemhunter.sources.gmgn import Gmgn  # noqa: E402

RANK = ["address", "symbol", "price", "market_cap", "liquidity", "volume", "swaps", "buys", "sells", "holder_count",
        "top_10_holder_rate", "rug_ratio", "is_wash_trading", "rat_trader_amount_rate", "bundler_rate", "entrapment_ratio",
        "sniper_count", "top70_sniper_hold_rate", "dev_team_hold_rate", "creator_token_status", "smart_degen_count",
        "renowned_count", "bluechip_owner_percentage", "history_highest_market_cap", "burn_status", "lock_percent",
        "renounced_mint", "is_honeypot", "is_open_source", "is_renounced", "buy_tax", "creator", "launchpad_platform",
        "creation_timestamp", "open_timestamp", "gas_fee", "bot_degen_rate", "cto_flag"]
TRENCH = ["address", "market_cap", "fresh_wallet_rate", "private_vault_hold_rate", "creator_created_count",
          "creator_created_open_count", "creator_balance_rate", "bundler_trader_amount_rate", "suspected_insider_hold_rate",
          "x_user_follower", "tg_call_count", "twitter_rename_count", "total_fee", "net_buy_24h", "complete_cost_time"]
HOLDER = ["address", "amount_percentage", "avg_cost", "unrealized_pnl", "buy_tx_count_cur", "buy_volume_cur", "netflow_usd",
          "sell_amount_percentage", "start_holding_at", "end_holding_at", "tags", "maker_token_tags", "native_transfer",
          "is_suspicious", "is_new", "transfer_in", "addr_type", "created_at", "realized_profit"]
FEED = ["transaction_hash", "maker", "side", "base_address", "amount_usd", "price_usd", "is_open_or_close", "timestamp"]
KLINE = ["time", "open", "high", "low", "close", "amount"]


def cover(name: str, rows: list[dict], want: list[str]) -> None:
    if not rows:
        print(f"  {name:<10} NO ROWS")
        return
    have = set().union(*(r.keys() for r in rows[:20]))
    missing = [k for k in want if k not in have]
    print(f"  {name:<10} {len(rows):>3} rows, {len(want) - len(missing)}/{len(want)} expected fields"
          + (f"  MISSING: {', '.join(missing)}" if missing else ""))


async def probe(g: Gmgn, cfg, chain: str) -> None:
    lo, hi = cfg.scope.mcap_min_usd, cfg.scope.mcap_max_usd
    print(f"\n== {chain}")
    flt = list(cfg.sources.gmgn.rank.filters.get("sol" if chain == "sol" else "evm") or [])
    rank = await g.rank(chain, "1h", "volume", 50, flt, lo, hi)
    cover("rank", rank, RANK)
    inside = [i for i in rank if lo <= (F.num(i.get("market_cap")) or 0) <= hi]
    if rank:
        print(f"             {len(inside)}/{len(rank)} inside ${lo:,.0f}-${hi:,.0f} (the server-side market-cap filter "
              f"{'works' if len(inside) == len(rank) else 'is NOT applied - filtered here instead'})")
    cover("trenches", await g.trenches(chain, 40, lo, hi), TRENCH)
    sig = await g.signals(chain, list(cfg.sources.gmgn.signals.types), lo, hi)
    print(f"  signals    {len(sig):>3} rows")
    for kind in ("smart", "kol"):
        cover(kind, await g.feed(kind, chain, 50), FEED)
    if not rank:
        return
    addr = str(rank[0]["address"])
    info = await g.info(chain, addr)
    print(f"  info       {'ok: ' + ', '.join(sorted(k for k in info if isinstance(info[k], dict))) if info else 'NO ANSWER'}")
    sec = await g.security(chain, addr)
    print(f"  security   {'ok, ' + str(len(sec)) + ' fields' if sec else 'NO ANSWER'}")
    cover("kline", await g.kline(chain, addr, "1h", 3) or [], KLINE)
    cover("holders", await g.holders(chain, addr, 50) or [], HOLDER)
    cover("traders", await g.traders(chain, addr, 50) or [], HOLDER)
    creator = (info or {}).get("dev", {}).get("creator_address") or rank[0].get("creator")
    if creator:
        made = await g.created_tokens(chain, str(creator))
        print(f"  created    {'ok: ' + ', '.join(sorted(made)) if made else 'NO ANSWER'}")


async def main() -> int:
    cfg = load_config()
    ap = argparse.ArgumentParser(description="Check GMGN routes and fields with your key")
    ap.add_argument("--chain", default=None)
    a = ap.parse_args()
    g = Gmgn(cfg.sources.gmgn, cfg.secret("GMGN_API_KEY"))
    if not g.enabled:
        print("GMGN_API_KEY is not set in .env")
        return 2
    try:
        for chain in ([a.chain] if a.chain else list(cfg.chains)):
            await probe(g, cfg, chain)
    finally:
        await g.close()
    print(f"\n{g.calls} requests, weight {g.spent}, {g.errors} errors, {g.limited} rate-limited"
          + (f"; not available: {sorted(g.unsupported)}" if g.unsupported else ""))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
