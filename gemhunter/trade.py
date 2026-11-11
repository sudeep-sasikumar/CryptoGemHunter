"""What happens after a snapshot or a call, tick by tick. Pure logic, no I/O.

Follow    grades a snapshot: did price reach the target multiple before the stop, inside the horizon?
Position  runs a call's trade plan (the ladder): milestone updates on the way up, exit on the stop,
          on the trailing stop once it is armed, on a safety trigger, or when time runs out.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Follow:
    snap_id: int
    entry: float                  # snapshot price worsened by the assumed slippage
    ts: float
    entry_mcap: float | None = None
    peak: float = 1.0
    low: float = 1.0
    last: float = 1.0
    hit: bool = False
    stopped: bool = False
    t_hit: float | None = None
    t_stop: float | None = None
    t_peak: float | None = None
    done: bool = False
    reason: str | None = None
    dirty: bool = False
    fast: int | None = None       # the fast grade: 1 hit, 0 not, None still open
    t_fast: float | None = None

    def tick(self, price: float | None, liq: float | None, ts: float, cfg) -> bool:
        """Feed one price. Returns True when the label is final."""
        if self.done:
            return True
        age = ts - self.ts
        if price and price > 0:
            m = price / self.entry
            self.last, self.dirty = m, True
            if m > self.peak:
                self.peak, self.t_peak = m, age
            self.low = min(self.low, m)
            if not self.hit and not self.stopped:
                if m >= cfg.target_mult:
                    self.hit, self.t_hit = True, age
                elif m <= cfg.stop_mult:
                    self.stopped, self.t_stop = True, age
            elif self.hit and m <= cfg.stop_mult and self.t_stop is None:
                self.t_stop = age                          # fell back after the hit: recorded, still a hit
            if self.fast is None:
                if m >= cfg.fast.target_mult:
                    self.fast, self.t_fast = 1, age
                elif m <= cfg.fast.stop_mult:
                    self.fast, self.t_fast = 0, age
        if self.fast is None and age >= cfg.fast.horizon_h * 3600:
            self.fast, self.t_fast, self.dirty = 0, age, True
        if liq is not None and liq < cfg.dead_liq_usd:
            return self.finish("liquidity gone", ts)
        if self.stopped:
            return self.finish("stopped", ts)
        if age >= cfg.horizon_h * 3600:
            return self.finish("horizon", ts)
        return False

    def finish(self, reason: str, ts: float) -> bool:
        if not self.done:
            self.done, self.reason, self.dirty = True, reason, True
            if self.fast is None:
                self.fast, self.t_fast = 0, ts - self.ts
            if reason == "liquidity gone" and not self.hit:
                self.stopped = True
                self.t_stop = self.t_stop if self.t_stop is not None else ts - self.ts
        return True


@dataclass
class Event:
    kind: str                     # update / warning / exit
    label: str
    mult: float
    price: float | None = None
    mcap: float | None = None
    detail: str = ""


@dataclass
class Position:
    call_id: int
    token_id: str
    ts: float
    entry: float                  # entry price incl. slippage
    entry_mcap: float | None
    entry_liq: float | None
    peak: float = 1.0
    peak_mcap: float | None = None
    last: float = 1.0
    last_mcap: float | None = None
    last_ts: float = 0.0
    sent: list = field(default_factory=list)       # milestones already announced
    warned: list = field(default_factory=list)
    armed: bool = False                             # trailing stop
    strikes: dict = field(default_factory=dict)     # exit condition -> polls in a row it has shown
    dev_closed_at_entry: int | None = None
    closed: bool = False

    def __post_init__(self):
        if self.peak_mcap is None:
            self.peak_mcap = self.entry_mcap

    def state(self) -> dict:
        return {"sent": self.sent, "warned": self.warned, "armed": self.armed,
                "dev_closed_at_entry": self.dev_closed_at_entry}

    def load_state(self, s: dict | None) -> None:
        s = s or {}
        self.sent, self.warned = list(s.get("sent") or []), list(s.get("warned") or [])
        self.armed, self.dev_closed_at_entry = bool(s.get("armed")), s.get("dev_closed_at_entry")

    def _confirmed(self, key: str, cond: bool, need: int) -> bool:
        self.strikes[key] = self.strikes.get(key, 0) + 1 if cond else 0
        return self.strikes[key] >= need

    def tick(self, price: float | None, mcap: float | None, liq: float | None, ts: float, plan) -> list[Event]:
        """One price poll -> the updates / exit it triggers (at most one exit, always last)."""
        if self.closed or not price or price <= 0:
            return []
        ev: list[Event] = []
        m = price / self.entry
        self.last, self.last_mcap, self.last_ts = m, mcap, ts
        if m > self.peak:
            self.peak = m
        if mcap and (self.peak_mcap is None or mcap > self.peak_mcap):
            self.peak_mcap = mcap
        for x in plan.update_mults:
            if m >= x and f"x{x:g}" not in self.sent:
                self.sent.append(f"x{x:g}")
                ev.append(Event("update", f"{x:g}x reached", m, price, mcap))
        for cap in plan.update_mcaps_usd:
            if mcap and mcap >= cap and (self.entry_mcap or 0) < cap and f"mc{cap:g}" not in self.sent:
                self.sent.append(f"mc{cap:g}")
                ev.append(Event("update", f"market cap passed ${cap / 1e6:g}M", m, price, mcap))
        if len(ev) > 1:                                    # a jump through several levels: announce the highest of each kind
            keep = {}
            for e in ev:
                keep["mc" if e.label.startswith("market cap") else "x"] = e
            ev = list(keep.values())
        if not self.armed and m >= plan.trail.arm_mult:
            self.armed = True
        need = int(plan.get("confirm_ticks") or 1)
        exit_ev = None
        if liq is not None and self.entry_liq and self._confirmed(
                "liq", liq <= self.entry_liq * (1 - plan.safety.liq_drop_pct / 100), need):
            exit_ev = Event("exit", "liquidity pulled", m, price, mcap,
                            f"liquidity ${liq:,.0f}, was ${self.entry_liq:,.0f} at entry")
        elif self._confirmed("stop", m <= 1 - plan.stop_pct / 100, need):
            exit_ev = Event("exit", f"stop -{plan.stop_pct:g}%", m, price, mcap)
        elif self.armed and self._confirmed("trail", m <= self.peak * (1 - plan.trail.drop_pct / 100), need):
            exit_ev = Event("exit", f"trailing stop ({plan.trail.drop_pct:g}% off the {self.peak:.1f}x peak)", m, price, mcap)
        elif ts - self.ts >= plan.max_days * 86400:
            exit_ev = Event("exit", f"{plan.max_days:g} days up", m, price, mcap)
        if exit_ev:
            self.closed = True
            ev.append(exit_ev)
        return ev

    def lost(self) -> list[Event]:
        """The pool disappeared: there is nothing left to sell."""
        if self.closed:
            return []
        self.closed, self.last = True, 0.0
        return [Event("exit", "liquidity pulled", 0.0, None, None, "the pool is gone: nothing left to sell")]

    def safety(self, f: dict, ts: float, plan) -> list[Event]:
        """A fresh look at the token (GMGN + smart-money feed) -> warnings or a safety exit."""
        if self.closed:
            return []
        s, ev, why = plan.safety, [], None
        closed = f.get("d_closed")
        if self.dev_closed_at_entry is None and closed is not None:
            self.dev_closed_at_entry = int(closed)
        if s.dev_dump and closed == 1 and self.dev_closed_at_entry == 0:
            why = "dev sold out"
        elif (f.get("s_rug_ratio") or 0) > s.rug_ratio:
            why = f"rug risk {f['s_rug_ratio']:.2f}"
        elif f.get("s_wash") == 1:
            why = "wash trading flagged"
        elif f.get("s_honeypot") == 1:
            why = "honeypot flagged"
        elif (f.get("w_sm_sellers_1h") or 0) >= s.smart_exit_wallets and (f.get("w_sm_net_usd_6h") or 0) < 0:
            why = f"smart money exiting ({f['w_sm_sellers_1h']:.0f} wallets selling)"
        if why:
            self.closed = True
            return [Event("exit", why, self.last, None, self.last_mcap)]
        for key, cond, text in (
                ("bundler", (f.get("h_sig_bundler_sell_n") or 0) > 0, "bundlers are selling"),
                ("kol_smart", f.get("w_kol_in_smart_out") == 1, "KOLs buying while smart money sells"),
                ("smart_sell", (f.get("w_sm_sellers_1h") or 0) >= 1 and (f.get("w_sm_net_usd_6h") or 0) < 0,
                 "smart money has started selling")):
            if cond and key not in self.warned:
                self.warned.append(key)
                ev.append(Event("warning", text, self.last, None, self.last_mcap))
        return ev
