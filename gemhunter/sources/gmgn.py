"""GMGN OpenAPI (openapi.gmgn.ai): read-only client. Needs GMGN_API_KEY; no request signing, never trades.

The key is shared with Launch Radar on the same VPS, so this client spends only `budget` of the plan
(0.2 x 5 = 1 weight per second by default), slows down when GMGN's remaining-budget header runs low and
sends nothing at all while a rate-limit pause runs (every request then would lengthen it, up to 5 minutes).
Weights are the documented ones, raised where Launch Radar measured more (token info / security 2, lists 3).
GMGN asks for IPv4, so the client binds to IPv4. Every string in a response (names, links, descriptions)
is written by token creators: data only.

Routes and field names follow GMGN's public skill documentation (gmgn-cli 1.5); tools/probe_gmgn.py checks
each one against a live answer with your key.
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid

import httpx

log = logging.getLogger("gmgn")
HOST = "https://openapi.gmgn.ai"
# quote currencies GMGN's own client sends per chain for the trenches feed
QUOTE_TYPES = {"sol": [4, 5, 3, 1, 13, 0], "bsc": [6, 7, 1, 16, 8, 3, 9, 10, 2, 17, 18, 0],
               "base": [11, 3, 12, 13, 0], "eth": [20, 11, 8, 3, 12, 1, 0], "robinhood": [11, 20, 24, 12, 0]}
WEIGHT = {"/v1/token/info": 2, "/v1/token/security": 2, "/v1/token/pool_info": 2, "/v1/trenches": 3,
          "/v1/market/rank": 3, "/v1/market/token_signal": 1, "/v1/market/token_kline": 2,
          "/v1/market/token_top_holders": 5, "/v1/market/token_top_traders": 5,
          "/v1/user/smartmoney": 1, "/v1/user/kol": 1, "/v1/user/created_tokens": 2, "/v1/user/wallet_stats": 3}
HEAVY = ("/v1/market/token_top_holders", "/v1/market/token_top_traders")


def _f(x) -> float | None:
    try:
        return None if x in (None, "") else float(x)
    except (TypeError, ValueError):
        return None


def rows(data, *keys) -> list[dict]:
    """The list inside a GMGN answer, whichever envelope it came in ({"list": [...]}, {"rank": [...]}, bare)."""
    if isinstance(data, dict):
        for k in keys + ("list", "data"):
            v = data.get(k)
            if isinstance(v, list):
                data = v
                break
            if isinstance(v, dict):
                return rows(v, *keys)
    return [r for r in data if isinstance(r, dict)] if isinstance(data, list) else []


class Gmgn:
    def __init__(self, cfg, api_key: str):
        self.cfg, self.key = cfg, api_key
        self.enabled = bool(cfg.enabled and api_key)
        self.plan = float(cfg.weight_per_sec)
        self.rate = max(self.plan * float(cfg.get("budget") or 0.2), 0.25)
        self.base_gap = 1.0 / self.rate
        self.gap = self.base_gap
        self._next = 0.0
        self._lock = asyncio.Lock()
        self.banned_until = 0.0
        self.heavy_until = 0.0
        self.calls = self.errors = self.limited = self.spent = self.strikes = 0
        self.remaining: float | None = None
        self.remaining_ts = 0.0
        self.shared_use = float(cfg.get("shared_use") or 0.0)      # share of the plan another program spends
        self.last_limit: dict | None = None
        self.unsupported: set[tuple[str, str]] = set()        # (path, chain) GMGN refused outright
        self.client = httpx.AsyncClient(
            timeout=httpx.Timeout(15.0, connect=6.0),
            transport=httpx.AsyncHTTPTransport(local_address="0.0.0.0", retries=1),     # IPv4 only
            headers={"X-APIKEY": api_key, "User-Agent": "GemHunter/1.0", "Content-Type": "application/json"})

    async def close(self) -> None:
        await self.client.aclose()

    def backlog_s(self) -> float:
        return max(0.0, self._next - time.monotonic(), self.banned_until - time.time())

    def heavy_ok(self) -> bool:
        return self.enabled and time.time() >= self.heavy_until

    async def _wait(self, weight: int, quiet: float = 0.0) -> None:
        """Take the next slot in the pacing queue. `quiet` seconds without any request of ours come first: a
        weight-5 list needs GMGN's whole free bucket (capacity 5), which only refills while we stay silent."""
        while True:
            ban = self.banned_until - time.time()
            if ban > 0:
                await asyncio.sleep(ban + 0.1)
                continue
            async with self._lock:
                now = time.monotonic()
                start = max(now, self._next) + quiet
                self._next = start + self.gap * weight
            if start > now:
                await asyncio.sleep(start - now)
            # GMGN told us what was left in the bucket after our last request. Launch Radar draws on the same
            # bucket, so only the part of the refill it leaves over is counted on; a request is sent when the
            # estimate covers its weight with one unit to spare (a refused request risks a ban for both programs).
            if self.remaining is not None:
                refill = max(self.plan * (1.0 - self.shared_use), 0.5)
                est = min(self.plan, self.remaining + (time.monotonic() - self.remaining_ts) * refill)
                need = min(weight + 1.0, self.plan)
                if est < need:
                    await asyncio.sleep(min((need - est) / refill, 20.0))
            if time.time() >= self.banned_until:
                return

    async def _call(self, method: str, path: str, query: dict | None = None, body: dict | None = None):
        chain = (query or {}).get("chain") or (body or {}).get("chain") or ""
        if not self.enabled or (path, chain) in self.unsupported:
            return None
        if path in HEAVY and not self.heavy_ok():
            return None
        weight = WEIGHT.get(path, 2)
        await self._wait(weight, float(self.cfg.get("heavy_quiet_s") or 0) if path in HEAVY else 0.0)
        q = dict(query or {}, timestamp=int(time.time()), client_id=str(uuid.uuid4()))
        self.calls += 1
        self.spent += weight
        try:
            r = await self.client.request(method, HOST + path, params=q, json=body)
        except (httpx.HTTPError, OSError) as e:
            self.errors += 1
            log.debug("%s failed: %s", path, e)
            return None
        left = _f(r.headers.get("x-ratelimit-remaining"))
        if left is not None:
            self.remaining, self.remaining_ts = left, time.monotonic()
        try:
            j = r.json()
        except ValueError:
            j = None
        if r.status_code == 429:
            self.limited += 1
            body_j = j if isinstance(j, dict) else {}
            code = str(body_j.get("error") or body_j.get("code") or 429)[:40]
            reset = _f(body_j.get("reset_at")) or _f(r.headers.get("x-ratelimit-reset"))
            banned = "BANNED" in code.upper()
            self.strikes += 1
            # A request sent during a ban lengthens it, and our clock is not GMGN's: wait well past the stated
            # reset, longer after every limit in a row (the key is shared: a ban here also stops Launch Radar).
            # Measured 2026-11-11 with Launch Radar on the same key: a ban outlasts its stated reset, because every
            # request either program sends meanwhile adds 5 s. So this program stays away for minutes, not seconds.
            base = max(reset - time.time() if reset else 0.0, float(self.cfg.get("ban_pause_s") or 120) if banned else 5.0)
            pause = min(base + (15.0 if banned else 5.0), 300.0) * min(2 ** (self.strikes - 1), 4)
            until = time.time() + min(pause, 600.0)
            if path in HEAVY:
                # the shared bucket rarely holds 5: leave the heavy lists alone for a while instead of retrying
                self.heavy_until = time.time() + float(self.cfg.get("heavy_cooldown_s") or 600)
            if until > self.banned_until + 1:
                log.warning("GMGN rate limit (%s) on %s - no GMGN calls for %.0f s", code, path, until - time.time())
            self.banned_until = max(self.banned_until, until)
            self._next = max(self._next, time.monotonic() + (until - time.time()))
            self.last_limit = {"ts": time.time(), "path": path, "code": code, "pause_s": round(until - time.time(), 1)}
            self.gap = min(self.gap * 1.5, 4 * self.base_gap)
            return None
        self.strikes = 0
        if left is not None and left <= 1:
            self.gap = min(self.gap * 1.25, 6 * self.base_gap)      # the bucket is empty after us: we are taking too much
        else:
            self.gap = max(self.base_gap, self.gap * 0.95)
        if j is None:
            self.errors += 1
            return None
        if r.status_code >= 400 or (isinstance(j, dict) and j.get("code") not in (0, None)):
            self.errors += 1
            text = str(j)[:200]
            if r.status_code == 403 or "not support" in text.lower() or "unsupported" in text.lower():
                self.unsupported.add((path, chain))
                log.warning("GMGN %s is not available for %s on this key: %s", path, chain or "-", text)
            else:
                log.debug("%s -> %s %s", path, r.status_code, text)
            return None
        return j.get("data") if isinstance(j, dict) and "data" in j else j

    # ---------------------------------------------------------------- lists
    async def rank(self, chain: str, interval: str, order_by: str, limit: int, filters: list[str],
                   mcap_min: float, mcap_max: float) -> list[dict]:
        """Tokens trading now inside the market-cap range, hottest first (GMGN "trending")."""
        q = {"chain": chain, "interval": interval, "order_by": order_by, "direction": "desc", "limit": limit,
             "min_marketcap": mcap_min, "max_marketcap": mcap_max}
        if filters:
            q["filters"] = list(filters)
        return [i for i in rows(await self._call("GET", "/v1/market/rank", q), "rank") if i.get("address")]

    async def trenches(self, chain: str, limit: int, mcap_min: float, mcap_max: float) -> list[dict]:
        """Graduated launchpad tokens inside the range, with GMGN's creator and wallet-label figures."""
        section = {"filters": ["offchain", "onchain"], "launchpad_platform_v2": True, "limit": limit,
                   "min_marketcap": mcap_min, "max_marketcap": mcap_max}
        if chain in QUOTE_TYPES:
            section["quote_address_type"] = QUOTE_TYPES[chain]
        data = await self._call("POST", "/v1/trenches", {"chain": chain}, {"version": "v2", "completed": section})
        return [i for i in rows(data, "completed") if i.get("address")]

    async def signals(self, chain: str, types: list[int], mcap_min: float, mcap_max: float) -> list[dict]:
        group = {"signal_type": list(types), "mc_min": mcap_min * 0.5, "mc_max": mcap_max * 20}
        data = await self._call("POST", "/v1/market/token_signal", None, {"chain": chain, "groups": [group]})
        return [i for i in rows(data) if i.get("token_address") and i.get("id")]

    async def feed(self, kind: str, chain: str, limit: int) -> list[dict]:
        """Recent smart-money ("smart") or KOL ("kol") trades on a chain."""
        path = "/v1/user/smartmoney" if kind == "smart" else "/v1/user/kol"
        return [i for i in rows(await self._call("GET", path, {"chain": chain, "limit": limit}))
                if i.get("base_address") and i.get("maker")]

    # ---------------------------------------------------------------- per token
    async def info(self, chain: str, address: str) -> dict | None:
        d = await self._call("GET", "/v1/token/info", {"chain": chain, "address": address})
        return d if isinstance(d, dict) and d.get("symbol") else None

    async def security(self, chain: str, address: str) -> dict | None:
        d = await self._call("GET", "/v1/token/security", {"chain": chain, "address": address})
        return d if isinstance(d, dict) and d else None

    async def holders(self, chain: str, address: str, limit: int = 100) -> list[dict] | None:
        d = await self._call("GET", "/v1/market/token_top_holders",
                             {"chain": chain, "address": address, "limit": limit, "order_by": "amount_percentage",
                              "direction": "desc"})
        return None if d is None else rows(d)

    async def traders(self, chain: str, address: str, limit: int = 100) -> list[dict] | None:
        d = await self._call("GET", "/v1/market/token_top_traders",
                             {"chain": chain, "address": address, "limit": limit, "order_by": "buy_volume_cur",
                              "direction": "desc"})
        return None if d is None else rows(d)

    async def kline(self, chain: str, address: str, resolution: str, days: float) -> list[dict] | None:
        now = time.time()
        d = await self._call("GET", "/v1/market/token_kline",
                             {"chain": chain, "address": address, "resolution": resolution,
                              "from": int((now - days * 86400) * 1000), "to": int(now * 1000)})   # milliseconds
        return None if d is None else rows(d)

    async def created_tokens(self, chain: str, wallet: str) -> dict | None:
        d = await self._call("GET", "/v1/user/created_tokens",
                             {"chain": chain, "wallet_address": wallet, "order_by": "token_ath_mc", "direction": "desc"})
        return d if isinstance(d, dict) else None

    async def wallet_stats(self, chain: str, wallets: list[str], period: str = "30d") -> list[dict]:
        d = await self._call("GET", "/v1/user/wallet_stats", {"chain": chain, "wallet_address": wallets, "period": period})
        return [d] if isinstance(d, dict) and "winrate" in d else rows(d)
