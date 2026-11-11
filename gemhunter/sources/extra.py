"""Two free sources beside GMGN and DexScreener.

GoPlus    - EVM contract read: the owner powers GMGN does not report (blacklist, pause, changeable tax,
            proxy, hidden owner) and a second opinion on honeypot / tax / LP lock.
CoinGecko - BTC and each chain's native coin, 24-hour change: the market the token trades in.
"""
from __future__ import annotations

DEAD = {"0x0000000000000000000000000000000000000000", "0x000000000000000000000000000000000000dead", ""}
NATIVE = {"sol": "solana", "eth": "ethereum", "base": "ethereum", "bsc": "binancecoin", "robinhood": "ethereum"}


def _f(x) -> float | None:
    try:
        return None if x in (None, "") else float(x)
    except (TypeError, ValueError):
        return None


def _flag(d: dict, k: str) -> int | None:
    v = d.get(k)
    return None if v in (None, "") else int(str(v) == "1")


class GoPlus:
    def __init__(self, http, cfg):
        self.http, self.on = http, bool(cfg.enabled)
        self.lim = http.limiter("goplus", cfg.calls_per_min)
        self.chain_ids = dict(cfg.chain_ids)

    async def check(self, chain: str, address: str) -> dict | None:
        cid = self.chain_ids.get(chain)
        if not (self.on and cid):
            return None
        data = await self.http.get_json(f"https://api.gopluslabs.io/api/v1/token_security/{cid}",
                                        params={"contract_addresses": address}, limiter=self.lim)
        try:
            d = next(iter(data["result"].values()))
        except (KeyError, TypeError, StopIteration, AttributeError):
            return None
        return parse_goplus(d)


def parse_goplus(d: dict) -> dict:
    buy, sell = _f(d.get("buy_tax")), _f(d.get("sell_tax"))
    owner = (d.get("owner_address") or "").lower()
    out = {
        "gp_ok": 1,
        "gp_honeypot": max(_flag(d, "is_honeypot") or 0, _flag(d, "cannot_sell_all") or 0, _flag(d, "cannot_buy") or 0)
        if any(d.get(k) not in (None, "") for k in ("is_honeypot", "cannot_sell_all", "cannot_buy")) else None,
        "gp_tax_max_pct": max(buy or 0, sell or 0) * 100 if (buy is not None or sell is not None) else None,
        "gp_open_source": _flag(d, "is_open_source"),
        "gp_proxy": _flag(d, "is_proxy"),
        "gp_mintable": _flag(d, "is_mintable"),
        "gp_blacklist": _flag(d, "is_blacklisted"),
        "gp_pausable": _flag(d, "transfer_pausable"),
        "gp_tax_modifiable": _flag(d, "slippage_modifiable"),
        "gp_hidden_owner": _flag(d, "hidden_owner"),
        "gp_take_back_owner": _flag(d, "can_take_back_ownership"),
        "gp_owner_change_balance": _flag(d, "owner_change_balance"),
        "gp_renounced": int(owner in DEAD) if "owner_address" in d and d.get("is_open_source") == "1" else None,
    }
    lp = d.get("lp_holders") or []
    if lp:
        out["gp_lp_locked_pct"] = sum(_f(h.get("percent")) or 0 for h in lp
                                      if h.get("is_locked") == 1 or (h.get("address") or "").lower() in DEAD) * 100
    out["gp_powers_n"] = sum(out.get(k) or 0 for k in ("gp_proxy", "gp_mintable", "gp_blacklist", "gp_pausable",
                                                      "gp_tax_modifiable", "gp_hidden_owner", "gp_take_back_owner",
                                                      "gp_owner_change_balance"))
    return out


class Market:
    """24-hour change of BTC and of each chain's native coin."""

    def __init__(self, http):
        self.http = http
        self.lim = http.limiter("coingecko", 4)
        self.chg: dict[str, float] = {}
        self.usd: dict[str, float] = {}

    async def refresh(self) -> None:
        ids = sorted({"bitcoin", *NATIVE.values()})
        d = await self.http.get_json("https://api.coingecko.com/api/v3/simple/price", limiter=self.lim,
                                     params={"ids": ",".join(ids), "vs_currencies": "usd", "include_24hr_change": "true"})
        if isinstance(d, dict):
            for k, v in d.items():
                c = _f((v or {}).get("usd_24h_change")) if isinstance(v, dict) else None
                if c is not None:
                    self.chg[k] = c
                u = _f(v.get("usd")) if isinstance(v, dict) else None
                if u:
                    self.usd[k] = u

    def factors(self, chain: str) -> dict:
        return {"mk_btc_chg_24h": self.chg.get("bitcoin"), "mk_native_chg_24h": self.chg.get(NATIVE.get(chain, "")),
                "_native_usd": self.usd.get(NATIVE.get(chain, ""))}


class Gecko:
    """GeckoTerminal (free, no key): hourly candles of a pool - the price structure without spending GMGN budget."""

    def __init__(self, http, cfg):
        self.http, self.on = http, bool(cfg.enabled)
        self.lim = http.limiter("geckoterminal", cfg.calls_per_min)
        self.networks = dict(cfg.networks)

    async def candles(self, chain: str, pool: str | None, hours: int = 168) -> list[dict] | None:
        net = self.networks.get(chain)
        if not (self.on and net and pool):
            return None
        d = await self.http.get_json(f"https://api.geckoterminal.com/api/v2/networks/{net}/pools/{pool}/ohlcv/hour",
                                     params={"aggregate": 1, "limit": min(int(hours), 1000), "currency": "usd"}, limiter=self.lim)
        try:
            rows = d["data"]["attributes"]["ohlcv_list"]
        except (KeyError, TypeError):
            return None
        out = []
        for r in rows or []:                                   # [time, open, high, low, close, volume in USD], newest first
            try:
                t, o, h, l, c, v = (float(x) for x in r[:6])
            except (TypeError, ValueError):
                continue
            if c > 0:
                out.append({"time": t, "open": o, "high": h, "low": l, "close": c, "volume": v, "amount": v / c})
        return sorted(out, key=lambda c: c["time"]) or None
