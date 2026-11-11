"""DexScreener (free, no key): price, market cap, liquidity, volume and buy / sell counts for up to 30 tokens
per request on any chain. This is what follows every watched token and open call between GMGN sweeps."""
from __future__ import annotations

BASE = "https://api.dexscreener.com"


def _f(x) -> float | None:
    try:
        return None if x in (None, "") else float(x)
    except (TypeError, ValueError):
        return None


class DexScreener:
    def __init__(self, http, cfg):
        self.http = http
        self.lim = http.limiter("dexscreener", cfg.calls_per_min)
        self.chain_ids = dict(cfg.chain_ids)

    async def pairs(self, chain: str, addresses: list[str]) -> dict[str, dict]:
        """Deepest pool of each token (parsed). Addresses DexScreener does not know are absent."""
        cid = self.chain_ids.get(chain)
        out: dict[str, dict] = {}
        if not cid:
            return out
        for i in range(0, len(addresses), 30):
            chunk = addresses[i:i + 30]
            data = await self.http.get_json(f"{BASE}/tokens/v1/{cid}/{','.join(chunk)}", limiter=self.lim)
            if not isinstance(data, list):
                continue
            wanted = {a.lower(): a for a in chunk}
            n_pools: dict[str, int] = {}
            for p in data:
                addr = wanted.get(((p.get("baseToken") or {}).get("address") or "").lower())
                if addr is None:
                    continue
                n_pools[addr] = n_pools.get(addr, 0) + 1
                liq = _f((p.get("liquidity") or {}).get("usd")) or 0
                if addr not in out or liq > (out[addr]["liq"] or 0):
                    out[addr] = parse_pair(p)
            for addr, n in n_pools.items():
                out[addr]["pools_n"] = n
        return out


def parse_pair(p: dict) -> dict:
    vol, tx, chg = p.get("volume") or {}, p.get("txns") or {}, p.get("priceChange") or {}
    info = p.get("info") or {}

    def side(win: str, k: str):
        return _f((tx.get(win) or {}).get(k))
    return {
        "price": _f(p.get("priceUsd")), "mcap": _f(p.get("marketCap")) or _f(p.get("fdv")), "fdv": _f(p.get("fdv")),
        "liq": _f((p.get("liquidity") or {}).get("usd")), "dex": str(p.get("dexId") or "")[:30],
        "pool": str(p.get("pairAddress") or "")[:80],
        "pool_created": (_f(p.get("pairCreatedAt")) or 0) / 1000 or None,
        "vol_5m": _f(vol.get("m5")), "vol_1h": _f(vol.get("h1")), "vol_6h": _f(vol.get("h6")), "vol_24h": _f(vol.get("h24")),
        "buys_5m": side("m5", "buys"), "sells_5m": side("m5", "sells"), "buys_1h": side("h1", "buys"),
        "sells_1h": side("h1", "sells"), "buys_6h": side("h6", "buys"), "sells_6h": side("h6", "sells"),
        "buys_24h": side("h24", "buys"), "sells_24h": side("h24", "sells"),
        "chg_5m": _f(chg.get("m5")), "chg_1h": _f(chg.get("h1")), "chg_6h": _f(chg.get("h6")), "chg_24h": _f(chg.get("h24")),
        "boosts": _f((p.get("boosts") or {}).get("active")),
        "paid_profile": int(bool(info.get("imageUrl") or info.get("socials") or info.get("websites"))),
    }
