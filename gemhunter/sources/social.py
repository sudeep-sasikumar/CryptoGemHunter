"""Community, website and fundamentals from free public sources - no keys, no paid data. Used at the deep look.

Telegram   t.me/<group> public page: members and how many are online now (community size and activity)
Website    is it live, how much is on it, does it sit on its own domain; domain age from RDAP (the registry record)
GitHub     a repository linked from the site or from CoinGecko: last push, stars, age, fork or original
CoinGecko  is the token listed (a listing is a catalyst and a review someone else did), its categories
           (the narrative, as CoinGecko files it), watchlist size, linked repositories
DefiLlama  protocols with real usage: TVL, audits on record, category

Every address fetched here was typed by a token creator. Only public http(s) hosts are fetched (no private,
loopback or link-local addresses, checked at every redirect), responses are size-capped, and nothing in a
response is treated as anything but data.
"""
from __future__ import annotations

import asyncio
import calendar
import ipaddress
import logging
import re
import socket
import time
from urllib.parse import urljoin, urlsplit

import httpx

log = logging.getLogger("social")
CG_PLATFORM = {"sol": "solana", "eth": "ethereum", "base": "base", "bsc": "binance-smart-chain"}
# hosts that are not a project's own site
SHARED_HOSTS = ("x.com", "twitter.com", "t.me", "telegram.me", "github.com", "github.io", "vercel.app", "netlify.app",
                "pump.fun", "linktr.ee", "carrd.co", "wixsite.com", "notion.site", "medium.com", "youtube.com", "tiktok.com",
                "instagram.com", "facebook.com", "reddit.com", "discord.gg", "discord.com", "zora.co", "bags.fm", "four.meme",
                "dexscreener.com", "gmgn.ai", "pages.dev", "web.app", "framer.website", "gitbook.io", "substack.com",
                "wikipedia.org", "google.com", "twitch.tv", "kick.com")
TWO_LEVEL = {"co", "com", "org", "net", "gov", "ac", "edu"}
TG = re.compile(r"(?:https?://)?(?:t|telegram)\.me/(?:s/)?(\+?[A-Za-z0-9_\-]{3,64})", re.I)
GH = re.compile(r"github\.com/([A-Za-z0-9_.\-]{1,60})/([A-Za-z0-9_.\-]{1,100})", re.I)
NUM = re.compile(r"([\d\s., ]+)\s*(members?|subscribers?|online)", re.I)


def _f(x) -> float | None:
    try:
        return None if x in (None, "") else float(x)
    except (TypeError, ValueError):
        return None


def tg_handle(link: str | None) -> str | None:
    m = TG.search(str(link or ""))
    return m.group(1) if m and m.group(1).lower() not in ("share", "joinchat", "addstickers") else None


def registrable(host: str) -> str:
    parts = host.lower().strip(".").split(".")
    if len(parts) >= 3 and len(parts[-1]) == 2 and parts[-2] in TWO_LEVEL:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def parse_telegram(html: str) -> dict:
    """ "12 345 members, 678 online" / "9 141 918 subscribers" from a t.me page."""
    m = re.search(r'tgme_page_extra">([^<]+)<', html or "")
    if not m:
        return {}
    out: dict = {}
    for num, word in NUM.findall(m.group(1)):
        digits = re.sub(r"\D", "", num)
        if digits:
            out["a_tg_online" if word.lower() == "online" else "a_tg_members"] = float(digits)
    if out.get("a_tg_members"):
        out["a_tg_is_channel"] = int("subscriber" in m.group(1).lower())          # a channel only broadcasts
        if out.get("a_tg_online") is not None:
            out["a_tg_online_pct"] = out["a_tg_online"] / out["a_tg_members"] * 100
    return out


def parse_site(html: str, status: int, host: str) -> dict:
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html or "", flags=re.S | re.I)
    words = len(re.sub(r"<[^>]+>", " ", text).split())
    low = (html or "").lower()
    own = not any(host == h or host.endswith("." + h) for h in SHARED_HOSTS)
    return {"a_web_live": int(status < 400), "a_web_words": float(words), "a_web_own_domain": int(own),
            "fu_web_docs": int(any(k in low for k in ("docs.", "/docs", "whitepaper", "litepaper", "gitbook"))),
            "fu_web_audit": int("audit" in low), "fu_web_app": int(any(k in low for k in ("launch app", "open app", "app.", "dapp"))),
            "_github": "/".join(GH.search(html).groups()) if html and GH.search(html) else None}


def parse_coingecko(d: dict) -> dict:
    links = d.get("links") or {}
    repos = [r for r in (links.get("repos_url") or {}).get("github") or [] if r]
    cats = [str(c) for c in d.get("categories") or [] if c]
    dev = d.get("developer_data") or {}
    return {"fu_cg_listed": 1, "fu_cg_watchlist": _f(d.get("watchlist_portfolio_users")),
            "fu_cg_categories_n": float(len(cats)), "fu_cg_up_pct": _f(d.get("sentiment_votes_up_percentage")),
            "fu_cg_commits_4w": _f(dev.get("commit_count_4_weeks")),
            "_cg_categories": cats[:20], "_github": "/".join(GH.search(repos[0]).groups()) if repos and GH.search(repos[0]) else None}


async def public_host(host: str) -> bool:
    """True only when every address the name resolves to is a public one."""
    if not host or host == "localhost":
        return False
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except (OSError, UnicodeError):
        return False
    try:
        return bool(infos) and all(ipaddress.ip_address(i[4][0]).is_global for i in infos)
    except ValueError:
        return False


class Social:
    def __init__(self, http, cfg):
        self.http, self.cfg = http, cfg
        self.on = bool(cfg.enabled)
        self.tg_lim = http.limiter("telegram-pages", 15)
        self.web_lim = http.limiter("websites", 20)
        self.rdap_lim = http.limiter("rdap", 15)
        self.gh_lim = http.limiter("github", 0.8)               # 60 an hour without a key
        self.cg_lim = http.limiter("coingecko", 4)
        self.llama: dict[str, dict] = {}
        self.llama_ts = 0.0
        self.domain_age: dict[str, float | None] = {}

    async def _text(self, url: str, limiter, max_bytes: int = 400_000, hops: int = 3) -> tuple[int, str, str] | None:
        """(status, body text, final host) of a public page, following redirects by hand so each hop is checked."""
        for _ in range(hops + 1):
            parts = urlsplit(url)
            if parts.scheme not in ("http", "https") or not await public_host(parts.hostname or ""):
                return None
            await limiter.wait()
            limiter.calls += 1
            try:
                async with self.http.client.stream("GET", url, timeout=8.0, follow_redirects=False,
                                                   headers={"Accept": "text/html,*/*"}) as r:
                    if r.status_code in (301, 302, 303, 307, 308) and r.headers.get("location"):
                        url = urljoin(url, r.headers["location"])
                        continue
                    buf = bytearray()
                    async for chunk in r.aiter_bytes():
                        buf += chunk
                        if len(buf) > max_bytes:
                            break
                    return r.status_code, bytes(buf[:max_bytes]).decode("utf-8", "replace"), (parts.hostname or "").lower()
            except (httpx.HTTPError, OSError, UnicodeError, ValueError):
                limiter.errors += 1
                return None
        return None

    async def telegram(self, link: str | None) -> dict:
        h = tg_handle(link)
        if not h:
            return {}
        r = await self._text(f"https://t.me/{h}", self.tg_lim, 200_000)
        return parse_telegram(r[1]) if r and r[0] == 200 else {}

    async def website(self, url: str | None) -> dict:
        url = str(url or "").strip()
        if not url or len(url) > 200 or tg_handle(url):
            return {}
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        r = await self._text(url, self.web_lim)
        if r is None:
            return {"a_web_live": 0}
        f = parse_site(r[1], r[0], r[2])
        if f["a_web_own_domain"] and f["a_web_live"]:
            age = await self.domain_age_days(registrable(r[2]))
            if age is not None:
                f["a_web_domain_age_d"] = age
        return f

    async def domain_age_days(self, domain: str) -> float | None:
        if domain in self.domain_age:
            return self.domain_age[domain]
        d = await self.http.get_json(f"https://rdap.org/domain/{domain}", limiter=self.rdap_lim, timeout=10.0)
        age = None
        for e in (d or {}).get("events") or [] if isinstance(d, dict) else []:
            if e.get("eventAction") == "registration" and e.get("eventDate"):
                try:
                    age = max(0.0, (time.time() - calendar.timegm(time.strptime(str(e["eventDate"])[:19], "%Y-%m-%dT%H:%M:%S"))) / 86400)
                except ValueError:
                    pass
        if len(self.domain_age) > 5000:
            self.domain_age.clear()
        self.domain_age[domain] = age
        return age

    async def github(self, repo: str | None) -> dict:
        if not repo or not re.fullmatch(r"[A-Za-z0-9_.\-]+/[A-Za-z0-9_.\-]+", repo):
            return {}
        d = await self.http.get_json(f"https://api.github.com/repos/{repo.removesuffix('.git')}", limiter=self.gh_lim)
        if not isinstance(d, dict) or "pushed_at" not in d:
            return {}

        def days(key: str) -> float | None:
            try:
                return max(0.0, (time.time() - calendar.timegm(time.strptime(d[key][:19], "%Y-%m-%dT%H:%M:%S"))) / 86400)
            except (KeyError, TypeError, ValueError):
                return None
        return {"fu_gh_repo": 1, "fu_gh_stars": _f(d.get("stargazers_count")), "fu_gh_pushed_d": days("pushed_at"),
                "fu_gh_age_d": days("created_at"), "fu_gh_fork": int(bool(d.get("fork")))}

    async def coingecko(self, chain: str, address: str) -> dict:
        platform = CG_PLATFORM.get(chain)
        if not platform:
            return {}
        d = await self.http.get_json(f"https://api.coingecko.com/api/v3/coins/{platform}/contract/{address}", limiter=self.cg_lim,
                                     params={"localization": "false", "tickers": "false", "market_data": "false",
                                             "community_data": "true", "developer_data": "true", "sparkline": "false"})
        return parse_coingecko(d) if isinstance(d, dict) and d.get("id") else {"fu_cg_listed": 0}

    async def refresh_llama(self) -> None:
        """DefiLlama's protocol list, once a day: token address -> usage on record."""
        if not self.cfg.get("defillama") or time.time() - self.llama_ts < 86400:
            return
        self.llama_ts = time.time()
        d = await self.http.get_json("https://api.llama.fi/protocols", timeout=60.0)
        if isinstance(d, list):
            book: dict[str, dict] = {}
            for p in d:                                      # one token can back several listed protocols: add them up
                if not isinstance(p, dict) or not p.get("address") or str(p.get("address")) in ("-", ""):
                    continue
                e = book.setdefault(str(p["address"]).split(":")[-1].lower(), {"fu_tvl_usd": None, "fu_audits_n": 0.0})
                if _f(p.get("tvl")) is not None:
                    e["fu_tvl_usd"] = (e["fu_tvl_usd"] or 0.0) + _f(p.get("tvl"))
                e["fu_audits_n"] = max(e["fu_audits_n"], _f(p.get("audits")) or 0.0)
            self.llama = {k: v for k, v in book.items() if v["fu_tvl_usd"] is not None}
            log.info("DefiLlama: %d tokens with usage on record", len(self.llama))

    async def look(self, chain: str, address: str, tg: str | None, web: str | None) -> dict:
        """Everything above for one token. A source that does not answer leaves its factors absent."""
        if not self.on:
            return {}
        f: dict = {}
        results = await asyncio.gather(self.telegram(tg), self.website(web), self.coingecko(chain, address),
                                       return_exceptions=True)
        repo = None
        for r in results:
            if isinstance(r, dict):
                repo = repo or r.pop("_github", None)
                f.update({k: v for k, v in r.items() if v is not None})
        if repo:
            try:
                f.update({k: v for k, v in (await self.github(repo)).items() if v is not None})
            except Exception:   # noqa: BLE001
                log.debug("github lookup failed", exc_info=True)
        hit = self.llama.get(address.lower())
        if hit:
            f.update({k: v for k, v in hit.items() if v is not None})
        # a token "claims a product" when it has a live site of its own with real content, code, usage or a listing
        f["fu_claims_product"] = int(bool((f.get("a_web_own_domain") and f.get("a_web_live") and (f.get("a_web_words") or 0) > 150)
                                          or f.get("fu_gh_repo") or f.get("fu_tvl_usd") or f.get("fu_web_docs")))
        return f
