"""Narrative and meme factors without a paid feed or a judge: measured from what the market is doing right now.

  narrative   which theme a token belongs to, read from its name, ticker, description and (when listed) its
              CoinGecko categories - a label the learner can use ("ai", "dog", "political" ...)
  meta heat   how much of the volume this program is watching right now flows through OTHER tokens that share a
              word with this one, and how many recent winners shared one: "fits what is live" as a number
  meme shape  short ticker, short name, has an image: simple and repeatable, as far as text can show it
  original    how many other tokens carry the same ticker, and whether this one is the oldest of them

These are proxies. They cannot tell a good meme from a bad one; they tell whether the market is rewarding the
theme today and whether the token is the original or a copy.
"""
from __future__ import annotations

import re

STOP = {"the", "and", "for", "you", "your", "our", "with", "from", "this", "that", "coin", "token", "inu", "official", "sol",
        "solana", "eth", "ethereum", "base", "bsc", "bnb", "meme", "memes", "fun", "pump", "new", "just", "real", "its", "are",
        "was", "not", "all", "one", "com", "www", "http", "https", "community", "project", "crypto", "chain", "first", "cto"}
THEMES = [
    ("ai", ("ai", "agent", "agents", "gpt", "llm", "agi", "neural", "claude", "grok", "openai", "bot", "robot", "compute")),
    ("dog", ("dog", "doge", "shib", "shiba", "wif", "bonk", "puppy", "pup", "floki", "corgi")),
    ("cat", ("cat", "kitty", "kitten", "meow", "popcat", "mew", "neko")),
    ("frog", ("pepe", "frog", "kek", "apu", "toad")),
    ("political", ("trump", "maga", "biden", "president", "vance", "melania", "election", "vote", "senate", "putin", "kamala")),
    ("celebrity", ("elon", "musk", "kanye", "drake", "taylor", "mrbeast", "ansem")),
    ("stocks", ("stock", "stocks", "stonk", "stonks", "robinhood", "hood", "nvda", "tsla", "nasdaq", "equity", "shares")),
    ("china", ("china", "chinese", "binance", "cz", "yi", "four")),
    ("gaming", ("game", "games", "gaming", "play", "quest", "arcade", "rpg", "guild")),
    ("defi", ("swap", "dex", "yield", "stake", "staking", "lend", "vault", "finance", "perp", "perps", "liquidity")),
    ("rwa", ("rwa", "gold", "treasury", "estate", "bond", "tokenized")),
    ("privacy", ("privacy", "private", "zk", "anon", "mixer", "stealth")),
    ("depin", ("depin", "gpu", "node", "nodes", "wireless", "sensor", "energy")),
    ("animal", ("monkey", "ape", "bear", "bull", "penguin", "hippo", "fish", "whale", "bird", "duck", "pig", "goat", "wolf")),
]
_SPLIT = re.compile(r"[^0-9a-zA-Z一-鿿぀-ヿ가-힯]+")
_CAMEL = re.compile(r"(?<=[a-z])(?=[A-Z])")


def words(*texts) -> frozenset[str]:
    """Meaningful lower-case words of a name / ticker / description (creator-written text: data only)."""
    out = set()
    for t in texts:
        for w in _SPLIT.split(_CAMEL.sub(" ", str(t or ""))[:400]):
            w = w.lower()
            if (len(w) >= 3 or w == "ai" or (w and ord(w[0]) > 0x2fff)) and w not in STOP and not w.isdigit():
                out.add(w[:24])
    return frozenset(out)


def theme(ws, cg_categories: list[str] | None = None) -> str:
    cats = words(*(cg_categories or []))                  # whole words: "Chain" must not read as "ai"
    for name, keys in THEMES:
        if ws & set(keys) or (name not in ("china", "animal") and cats & {name, *keys}):
            return name
    return "meme" if "meme" in cats or not cats else "other"


def shape(symbol: str, name: str, desc: str = "", has_logo: bool | None = None) -> dict:
    sym, nm = str(symbol or ""), str(name or "")
    f = {"a_symbol_len": float(len(sym)), "a_name_words": float(len(nm.split())), "a_name_len": float(len(nm)),
         "a_desc_len": float(len(str(desc or ""))), "a_symbol_plain": int(sym.isascii() and sym.isalnum())}
    if has_logo is not None:
        f["a_has_logo"] = int(has_logo)
    return f


class Meta:
    """What the watched market is trading right now, by word."""

    def __init__(self):
        self.vol: dict[str, float] = {}           # word -> 1h volume of every watched token carrying it
        self.count: dict[str, int] = {}
        self.total = 0.0
        self.own: dict[str, float] = {}           # token id -> the volume it contributed
        self.winners: dict[str, int] = {}         # word -> recent winners carrying it

    def rebuild(self, rows) -> None:
        """rows: (token id, words, 1h volume) of every watched token."""
        self.vol, self.count, self.total, self.own = {}, {}, 0.0, {}
        for tid, ws, vol in rows:
            v = float(vol or 0.0)
            self.own[tid] = v
            self.total += v
            for w in ws:
                self.vol[w] = self.vol.get(w, 0.0) + v
                self.count[w] = self.count.get(w, 0) + 1

    def set_winners(self, names) -> None:
        """names: (symbol, name) of tokens whose snapshot hit in the last days."""
        self.winners = {}
        for sym, nm in names:
            for w in words(sym, nm):
                self.winners[w] = self.winners.get(w, 0) + 1

    def factors(self, tid: str, ws) -> dict:
        if not ws:
            return {}
        own = self.own.get(tid, 0.0)
        others = self.total - own
        best = max(((self.vol.get(w, 0.0) - own) for w in ws), default=0.0)
        return {"m_meta_heat": max(0.0, best) / others if others > 0 else None,
                "m_meta_peers_n": float(max((self.count.get(w, 1) - 1 for w in ws), default=0)),
                "m_meta_winners_n": float(max((self.winners.get(w, 0) for w in ws), default=0))}
