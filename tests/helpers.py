"""Test doubles: an engine on a temporary database with every network source replaced by canned answers
shaped like GMGN's documented responses."""
from __future__ import annotations

import time

from gemhunter.config import apply_overrides, load_config
from gemhunter.engine import Engine

ADDR = "GemTokenAddr1111111111111111111111111111111"
CREATOR = "Creator111111111111111111111111111111111111"


def rank_item(address: str = ADDR, **over) -> dict:
    now = time.time()
    d = {"address": address, "symbol": "GEM", "name": "Gem Token", "chain": "sol", "price": "0.0003", "market_cap": 300000,
         "liquidity": 60000, "volume": 90000, "swaps": 900, "buys": 520, "sells": 380, "holder_count": 1500,
         "top_10_holder_rate": 0.16, "rug_ratio": 0.05, "is_wash_trading": False, "rat_trader_amount_rate": 0.02,
         "bundler_rate": 0.03, "entrapment_ratio": 0.01, "sniper_count": 4, "top70_sniper_hold_rate": 0.005,
         "dev_team_hold_rate": 0.0, "creator_token_status": "creator_close", "creator_close": True, "smart_degen_count": 5,
         "renowned_count": 2, "bluechip_owner_percentage": 0.1, "history_highest_market_cap": 520000, "burn_status": "burn",
         "renounced_mint": 1, "renounced_freeze_account": 1, "is_honeypot": "", "buy_tax": "", "sell_tax": "",
         "creator": CREATOR, "launchpad_platform": "Pump.fun", "creation_timestamp": now - 30 * 3600,
         "open_timestamp": now - 29 * 3600, "gas_fee": 2500, "bot_degen_rate": 0.05, "cto_flag": 0, "total_supply": 1e9,
         "twitter_username": "gemtoken", "telegram": "t.me/gem", "website": ""}
    d.update(over)
    return d


def holder(i: int, share: float, **over) -> dict:
    now = time.time()
    d = {"address": f"Wallet{i:03d}", "addr_type": 0, "amount_percentage": share, "avg_cost": 0.00028, "buy_tx_count_cur": 2,
         "buy_volume_cur": 400 + i, "sell_tx_count_cur": 0, "sell_amount_percentage": 0.0, "netflow_usd": -400,
         "start_holding_at": now - 20 * 3600 + i * 60, "end_holding_at": None, "tags": [], "maker_token_tags": [],
         "native_transfer": {"address": f"Funder{i:03d}"}, "is_suspicious": False, "is_new": False, "transfer_in": False,
         "created_at": now - 200 * 86400, "realized_profit": 0, "usd_value": 3000}
    d.update(over)
    return d


def holders_list() -> list[dict]:
    rows = [{"address": "PoolAddr", "addr_type": 2, "exchange": "pump_amm", "amount_percentage": 0.2}]
    rows += [holder(i, 0.012) for i in range(40)]
    for i in range(4):
        rows[1 + i]["tags"] = ["smart_degen"]
    return rows


def candles(n: int = 60, price: float = 0.0003) -> list[dict]:
    now, out = time.time(), []
    for i in range(n):
        p = price * (0.6 + 0.4 * i / n)
        out.append({"time": int(now - (n - i) * 3600), "open": p * 0.99, "high": p * 1.03, "low": p * 0.97, "close": p,
                    "volume": 4000 + 50 * i, "amount": (4000 + 50 * i) / p})
    return out


class FakeGmgn:
    enabled = True
    calls = errors = limited = spent = skipped = 0
    remaining = None
    last_limit = None
    rate = 1.0
    heavy_until = 0.0
    banned_until = 0.0

    def __init__(self):
        self.unsupported = set()
        self.rank_items = {"sol": [rank_item()]}
        self.sec = {"is_honeypot": "", "open_source": "unknown", "owner_renounced": "unknown", "renounced_mint": True,
                    "renounced_freeze_account": True, "buy_tax": "0", "sell_tax": "0", "burn_status": "burn", "rug_ratio": 0.05,
                    "is_wash_trading": False, "top_10_holder_rate": 0.16, "creator_token_status": "creator_close"}
        self.info_over: dict = {}

    def backlog_s(self): return 0.0
    def heavy_ok(self): return True
    async def close(self): pass
    async def rank(self, chain, *a, **k): return self.rank_items.get(chain, [])
    async def trenches(self, chain, *a, **k): return []
    async def signals(self, chain, *a, **k): return []
    async def feed(self, kind, chain, limit): return []

    async def info(self, chain, address):
        d = {"symbol": "GEM", "name": "Gem Token", "address": address, "circulating_supply": 1e9, "total_supply": 1e9,
             "liquidity": 60000, "holder_count": 1500, "creation_timestamp": time.time() - 30 * 3600, "launchpad_status": 2,
             "launchpad_progress": 1, "launchpad_platform": "Pump.fun", "price": {"price": "0.0003", "buys_1h": 520, "sells_1h": 380,
                                                                               "buy_volume_1h": 60000, "sell_volume_1h": 30000},
             "dev": {"creator_address": CREATOR, "creator_token_status": "sell", "creator_open_count": 1},
             "stat": {"holder_count": 1500, "top_10_holder_rate": 0.16, "dev_team_hold_rate": 0, "fresh_wallet_rate": 0.1},
             "wallet_tags_stat": {"smart_wallets": 5, "renowned_wallets": 2}, "link": {"twitter_username": "gemtoken"}}
        d.update(self.info_over)
        return d

    async def security(self, chain, address): return dict(self.sec)
    async def holders(self, chain, address, limit=100): return holders_list()
    async def traders(self, chain, address, limit=100): return holders_list()
    async def kline(self, chain, address, resolution, days): return candles()

    async def created_tokens(self, chain, wallet):
        return {"inner_count": 0, "open_count": 2, "open_ratio": "1", "tokens": [
            {"token_address": "Other1", "market_cap": "150000", "token_ath_mc": "900000", "create_timestamp": time.time() - 40 * 86400}]}


class FakeDex:
    def __init__(self):
        self.price, self.liq = 0.0003, 60000.0

    async def pairs(self, chain, addresses):
        return {a: {"price": self.price, "mcap": self.price * 1e9, "fdv": self.price * 1e9, "liq": self.liq, "pools_n": 1,
                    "vol_1h": 90000, "vol_24h": 600000, "buys_1h": 520, "sells_1h": 380, "buys_24h": 4000, "sells_24h": 3500,
                    "buys_5m": 50, "sells_5m": 30, "vol_5m": 9000, "chg_1h": 6.0} for a in addresses}


class FakeGoPlus:
    async def check(self, chain, address): return None


class FakeGecko:
    async def candles(self, chain, pool, hours=168): return None


class FakeSocial:
    async def look(self, chain, address, tg, web):
        return {"a_tg_members": 2400.0, "a_tg_online": 120.0, "a_tg_online_pct": 5.0, "a_tg_is_channel": 0, "a_web_live": 1,
                "a_web_own_domain": 1, "a_web_words": 600.0, "a_web_domain_age_d": 40.0, "fu_cg_listed": 0, "fu_claims_product": 1}

    async def refresh_llama(self): pass


class FakeTelegram:
    enabled, sent, failed = True, 0, 0

    def __init__(self):
        self.messages: list[tuple[str, str, dict | None]] = []

    async def send(self, text, *, dedupe, kind, reply_to=None, silent=False):
        if any(m[3] == dedupe for m in self.messages):
            return None
        self.messages.append((kind, text, reply_to, dedupe))
        self.sent += 1
        return {"chat": len(self.messages)}

    async def close(self): pass


def make_engine(tmp_path, overrides: str = "") -> Engine:
    cfg = load_config()
    apply_overrides(cfg, f"app.data_dir={tmp_path.as_posix()};call.warmup_snapshots=0;{overrides}")
    eng = Engine(cfg)
    eng.gmgn, eng.dex, eng.goplus, eng.telegram, eng.social = FakeGmgn(), FakeDex(), FakeGoPlus(), FakeTelegram(), FakeSocial()
    eng.gecko = FakeGecko()
    return eng
