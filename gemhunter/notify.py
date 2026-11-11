"""Telegram: one message per call (entry), a reply under it for every trade update, warning and the exit.

Env: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID (comma-separated to post to several chats). Every message has a
dedupe key recorded in the database, so a restart never repeats one. Without a token the text is printed
to the console instead. Token names and symbols are written by creators: always escaped.
"""
from __future__ import annotations

import asyncio
import html
import json
import logging
import re
import time

import httpx

log = logging.getLogger("telegram")
API = "https://api.telegram.org"
CHAIN_NAME = {"sol": "Solana", "eth": "Ethereum", "base": "Base", "bsc": "BSC", "robinhood": "Robinhood"}
DEX_CHAIN = {"sol": "solana", "eth": "ethereum", "base": "base", "bsc": "bsc", "robinhood": "robinhood"}


class Telegram:
    def __init__(self, cfg, token: str, chat_id: str, db):
        self.cfg, self.token, self.db = cfg, token, db
        self.chats = [c.strip() for c in str(chat_id or "").split(",") if c.strip()]
        self.thread_id = cfg.get("thread_id")
        self.enabled = bool(cfg.enabled and token and self.chats)
        self.client = httpx.AsyncClient(timeout=40.0)
        self._lock = asyncio.Lock()
        self._last = 0.0
        self.sent = self.failed = 0

    async def close(self) -> None:
        await self.client.aclose()

    async def send(self, text: str, *, dedupe: str, kind: str, reply_to: dict | None = None,
                   silent: bool = False) -> dict | None:
        """Post to every configured chat once per dedupe key. Returns {chat id: message id}."""
        if self.db.one("SELECT 1 FROM alerts WHERE dedupe=?", (dedupe,)):
            return None
        ids: dict = {}
        if not self.enabled:
            print(f"\n[telegram not configured - console only]\n{plain(text)}\n", flush=True)
            status = "console"
        else:
            for chat in self.chats:
                mid = await self._post(chat, text, (reply_to or {}).get(chat), silent)
                if mid:
                    ids[chat] = mid
            status = "sent" if ids else "failed"
            self.failed += status == "failed"
        self.db.execute("INSERT OR REPLACE INTO alerts(dedupe,ts,kind,text,msg_id,status) VALUES(?,?,?,?,?,?)",
                        (dedupe, time.time(), kind, text, json.dumps(ids) if ids else None, status))
        self.sent += 1
        return ids or None

    async def _post(self, chat: str, text: str, reply_to: int | None, silent: bool = False) -> int | None:
        payload: dict = {"chat_id": chat, "text": text[:4096], "parse_mode": "HTML",
                         "link_preview_options": {"is_disabled": True}, "disable_notification": silent}
        if self.thread_id:
            payload["message_thread_id"] = int(self.thread_id)
        if reply_to:
            payload["reply_parameters"] = {"message_id": int(reply_to), "allow_sending_without_reply": True}
        async with self._lock:
            for attempt in range(4):
                wait = 1.1 - (time.monotonic() - self._last)
                if wait > 0:
                    await asyncio.sleep(wait)
                try:
                    r = await self.client.post(f"{API}/bot{self.token}/sendMessage", json=payload)
                    self._last = time.monotonic()
                    j = r.json()
                except (httpx.HTTPError, ValueError) as e:
                    log.warning("telegram send failed (%s), retry %d", type(e).__name__, attempt + 1)
                    await asyncio.sleep(2 ** attempt)
                    continue
                if j.get("ok"):
                    return int(j["result"]["message_id"])
                if j.get("error_code") == 429:
                    await asyncio.sleep(float(j.get("parameters", {}).get("retry_after", 5)))
                    continue
                log.error("telegram rejected message: %s", j.get("description"))
                return None
        return None


def message_ids(stored) -> dict:
    try:
        v = json.loads(stored) if stored else {}
        return v if isinstance(v, dict) else {}
    except (TypeError, ValueError):
        return {}


def plain(text: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", text))


def esc(s) -> str:
    return html.escape(str(s or ""), quote=False)


def money(x: float | None) -> str:
    if x is None:
        return "?"
    if x >= 1e6:
        return f"${x / 1e6:.2f}M"
    if x >= 1e3:
        return f"${x / 1e3:.1f}k"
    return f"${x:.0f}"


def price_txt(p: float | None) -> str:
    return "?" if not p else f"${p:.4g}" if p >= 0.0001 else f"${p:.3e}"


def links(chain: str, address: str, f: dict | None = None) -> str:
    out = [f'<a href="https://gmgn.ai/{chain}/token/{address}">GMGN</a>',
           f'<a href="https://dexscreener.com/{DEX_CHAIN.get(chain, chain)}/{address}">DexScreener</a>']
    if chain == "sol":
        out.append(f'<a href="https://rugcheck.xyz/tokens/{address}">RugCheck</a>')
    x = (f or {}).get("_x")
    if x and re.fullmatch(r"[A-Za-z0-9_]{1,30}", str(x)):
        out.append(f'<a href="https://x.com/{x}">X</a>')
    return " · ".join(out)


def entry_text(tok, f: dict, score: float, prob: float | None, model_ver: str | None, good: list[str],
               bad: list[str], entry_price: float, plan, label) -> str:
    def p(k, d=0):
        v = f.get(k)
        return "?" if v is None else f"{v:.{d}f}%"
    age = f.get("p_age_h")
    age_txt = "?" if age is None else f"{age:.0f} h" if age < 72 else f"{age / 24:.0f} d"
    lines = [f"💎 <b>GEM CALL — ENTRY</b>",
             f"<b>{esc(tok.symbol)}</b> — {esc(tok.name)}",
             f"{CHAIN_NAME.get(tok.chain, tok.chain)} · {esc(tok.launchpad or 'no launchpad')} · {age_txt} old",
             f"Entry {price_txt(entry_price)} · mcap <b>{money(f.get('_mcap'))}</b> · liquidity {money(f.get('l_liq_usd'))}"
             + (f" · {f['h_holders']:.0f} holders" if f.get("h_holders") else "")]
    if prob is not None:
        lines.append(f"Chance of {label.target_mult:g}x before -{(1 - label.stop_mult) * 100:.0f}%: <b>{prob * 100:.0f}%</b> "
                     f"(model {esc(model_ver)}) · scorecard {score:.0f}/100")
    else:
        lines.append(f"Scorecard <b>{score:.0f}/100</b> (learning - no model yet)")
    lines.append(f"Top10 {p('h_top10_pct')} · Dev {p('d_team_pct', 1)}" + (" (sold)" if f.get("d_closed") == 1 else "")
                 + f" · Bundlers {p('h_bundler_pct')} · Insiders {p('h_insider_pct')} · Entrapment {p('h_entrapment_pct')} · "
                 f"Snipers hold {p('h_sniper_hold_pct')}")
    smart = f.get("w_smart_bought_n") if f.get("w_smart_bought_n") else f.get("w_smart_n")
    buzz = []
    if smart:
        buzz.append(f"{smart:.0f} smart holders bought" if f.get("w_smart_bought_n")
                    else f"{smart:.0f} smart wallets (GMGN count)")
    if f.get("w_sm_converge_n", 0) >= 2:
        buzz.append(f"{f['w_sm_converge_n']:.0f} smart buys within 30 min")
    if f.get("w_kol_n"):
        buzz.append(f"{f['w_kol_n']:.0f} KOLs")
    if f.get("own_proven_n"):
        buzz.append(f"{f['own_proven_n']:.0f} of our proven wallets early")
    if f.get("f_buy_ratio_n_1h") is not None:
        buzz.append(f"{f['f_buy_ratio_n_1h'] * 100:.0f}% buys (1h)")
    if f.get("f_vol_1h"):
        buzz.append(f"vol 1h {money(f['f_vol_1h'])}")
    if buzz:
        lines.append(" · ".join(buzz))
    crowd = []
    if f.get("a_tg_members"):
        crowd.append(f"Telegram {f['a_tg_members']:,.0f}" + (f" ({f['a_tg_online']:,.0f} online)" if f.get("a_tg_online") else ""))
    if f.get("a_x_followers"):
        crowd.append(f"X {f['a_x_followers']:,.0f} followers")
    if f.get("narrative") and f["narrative"] not in ("meme", "other"):
        crowd.append(f"theme: {esc(f['narrative'])}" + (f" ({f['m_meta_heat'] * 100:.0f}% of watched volume)" if f.get("m_meta_heat") else ""))
    if f.get("a_symbol_twins_n"):
        crowd.append("original ticker" if f.get("a_symbol_oldest") else f"{f['a_symbol_twins_n']:.0f} other tokens share the ticker")
    if f.get("a_web_live") and f.get("a_web_own_domain"):
        crowd.append("own site" + (f", domain {f['a_web_domain_age_d']:.0f} d old" if f.get("a_web_domain_age_d") is not None else ""))
    if f.get("fu_cg_listed"):
        crowd.append("on CoinGecko")
    if f.get("fu_gh_repo"):
        crowd.append("code pushed " + (f"{f['fu_gh_pushed_d']:.0f} d ago" if f.get("fu_gh_pushed_d") is not None else "?"))
    if crowd:
        lines.append(" · ".join(crowd))
    sec = []
    if f.get("s_checked") or f.get("gp_ok"):
        sec.append("no honeypot")
        sec.append(f"tax {max(f.get('s_tax_max_pct') or 0, f.get('gp_tax_max_pct') or 0):.0f}%")
        if tok.chain == "sol":
            sec.append("mint + freeze revoked")
        else:
            sec.append("verified" if f.get("s_open_source") or f.get("gp_open_source") else "verification unknown")
            if f.get("s_owner_renounced") is not None:
                sec.append("renounced" if f["s_owner_renounced"] else "owner active")
    if f.get("l_on_curve"):
        sec.append("liquidity in bonding curve")
    elif f.get("s_lp_burned"):
        sec.append("LP burned")
    elif f.get("s_lp_locked_pct") is not None:
        sec.append(f"LP locked {f['s_lp_locked_pct']:.0f}%")
    lines.append("Contract: " + (" · ".join(sec) if sec else "not checked"))
    if f.get("d_created_n") is not None:
        lines.append(f"Deployer has made {f['d_created_n']:.0f} token{'s' if f['d_created_n'] != 1 else ''}"
                     + (f", {f['d_open_n']:.0f} graduated" if f.get("d_open_n") is not None else "")
                     + (f", best reached {money(f['d_best_ath_mcap'])}" if f.get("d_best_ath_mcap") else ""))
    if good:
        lines.append("✅ " + ", ".join(good))
    if bad:
        lines.append("⚠️ " + ", ".join(bad))
    lines.append(f"📋 Plan: stop -{plan.stop_pct:g}% · past {plan.trail.arm_mult:g}x a {plan.trail.drop_pct:g}% trailing stop · "
                 f"updates at {', '.join(f'{m:g}x' for m in plan.update_mults[:4])} and $1M / $2M / $5M · {plan.max_days:g} days max")
    lines.append(f"<code>{esc(tok.address)}</code>")
    lines.append(links(tok.chain, tok.address, f))
    lines.append("<i>Signal only. Not financial advice.</i>")
    return "\n".join(lines)


def event_text(symbol: str, ev, entry_price: float, held_s: float, peak: float) -> str:
    held = f"{held_s / 3600:.1f} h" if held_s < 172800 else f"{held_s / 86400:.1f} d"
    pnl = (ev.mult - 1) * 100
    now = f"now {ev.mult:.2f}x ({pnl:+.0f}%)" + (f" · mcap {money(ev.mcap)}" if ev.mcap else "")
    if ev.kind == "exit":
        icon = "✅" if ev.mult >= 1 else "🛑"
        return (f"{icon} <b>EXIT — {esc(symbol)}</b>\nReason: {esc(ev.label)}\nResult: <b>{ev.mult:.2f}x ({pnl:+.0f}%)</b> "
                f"after {held} · best was {peak:.2f}x" + (f"\n{esc(ev.detail)}" if ev.detail else ""))
    if ev.kind == "warning":
        return f"⚠️ <b>{esc(symbol)}</b> — {esc(ev.label)}\n{now} · held {held}"
    return f"📈 <b>TRADE UPDATE — {esc(symbol)}</b>\n{esc(ev.label)}\n{now} · held {held}"
