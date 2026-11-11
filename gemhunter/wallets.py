"""This program's own history, the part of the spec no public tag can give:

  proven wallets   wallets that were early in tokens that later ran. Every token that got a deep look has its
                   earliest real buyers recorded; when the token's snapshots are all graded, each of those
                   wallets is credited with a token and, if it hit, a win. A wallet is "proven" once it has
                   enough tokens and wins far more often than the average early buyer.
  deployers        every deployer seen, how its tokens ended, and whether a call on one ended on a safety exit.
"""
from __future__ import annotations


class History:
    def __init__(self, db, cfg):
        self.db, self.cfg = db, cfg
        self.base = 0.05
        self.refresh_base()

    def refresh_base(self) -> None:
        r = self.db.one("SELECT SUM(n_win) w, SUM(n_tok) t FROM wallets")
        if r and r["t"]:
            self.base = max(0.01, (r["w"] or 0) / r["t"])

    def is_proven(self, n_tok: int, n_win: int) -> bool:
        if n_tok < self.cfg.min_tokens or n_win < 2:
            return False
        return (n_win + 1) / (n_tok + 4) >= self.cfg.min_lift * self.base       # shrunk towards a low rate

    # -- recording
    def record_early(self, token_id: str, buyers: list[tuple[str, float, float]]) -> None:
        self.db.executemany("INSERT OR IGNORE INTO token_wallets(token_id, addr, first_ts, buy_usd) VALUES(?,?,?,?)",
                            [(token_id, a, t, usd) for a, t, usd in buyers])

    def credit(self, token_id: str, creator: str, hit: bool, dead: bool, best_mult: float, ts: float) -> None:
        """All snapshots of a token are graded: credit its early buyers and its deployer once."""
        self.db.execute(
            "INSERT INTO wallets(addr, n_tok, n_win, last_ts) SELECT addr, 1, ?, ? FROM token_wallets WHERE token_id=? "
            "ON CONFLICT(addr) DO UPDATE SET n_tok=n_tok+1, n_win=n_win+excluded.n_win, last_ts=excluded.last_ts",
            (int(hit), ts, token_id))
        if creator:
            self.db.execute(
                "INSERT INTO deployers(addr, n_tok, n_win, n_dead, best_mult, last_ts) VALUES(?,1,?,?,?,?) "
                "ON CONFLICT(addr) DO UPDATE SET n_tok=n_tok+1, n_win=n_win+excluded.n_win, n_dead=n_dead+excluded.n_dead, "
                "best_mult=MAX(best_mult, excluded.best_mult), last_ts=excluded.last_ts",
                (creator, int(hit), int(dead), best_mult, ts))
        self.db.execute("UPDATE tokens SET credited=1 WHERE id=?", (token_id,))

    def call_closed(self, creator: str, rugged: bool, ts: float) -> None:
        if creator:
            self.db.execute(
                "INSERT INTO deployers(addr, n_called, n_rug, last_ts) VALUES(?,1,?,?) "
                "ON CONFLICT(addr) DO UPDATE SET n_called=n_called+1, n_rug=n_rug+excluded.n_rug, last_ts=excluded.last_ts",
                (creator, int(rugged), ts))

    # -- factors
    def factors(self, token_id: str, creator: str, buyers: list[tuple[str, float, float]] | None) -> dict:
        f: dict = {}
        if creator:
            d = self.db.one("SELECT * FROM deployers WHERE addr=?", (creator,))
            f.update({"own_dev_prev_n": d["n_tok"] if d else 0, "own_dev_win_n": d["n_win"] if d else 0,
                      "own_dev_dead_n": d["n_dead"] if d else 0, "own_dev_rug_n": d["n_rug"] if d else 0})
        if buyers:
            addrs = [b[0] for b in buyers]
            known = self.db.query(f"SELECT addr, n_tok, n_win FROM wallets WHERE addr IN ({','.join('?' * len(addrs))})", tuple(addrs))
            seasoned = [r for r in known if r["n_tok"] >= self.cfg.min_tokens]
            f["own_known_n"] = len(seasoned)
            f["own_proven_n"] = sum(1 for r in seasoned if self.is_proven(r["n_tok"], r["n_win"]))
            f["own_losers_n"] = sum(1 for r in seasoned if r["n_win"] == 0 and r["n_tok"] >= 2 * self.cfg.min_tokens)
        return f

    def top(self, limit: int = 25) -> list[dict]:
        rows = self.db.query("SELECT addr, n_tok, n_win, last_ts FROM wallets WHERE n_tok>=? AND n_win>=2 "
                             "ORDER BY (n_win+1.0)/(n_tok+4) DESC LIMIT 300", (self.cfg.min_tokens,))
        out = [{**dict(r), "lift": round((r["n_win"] + 1) / (r["n_tok"] + 4) / self.base, 2)} for r in rows
               if self.is_proven(r["n_tok"], r["n_win"])]
        return out[:limit]
