"""The numbers of screen 3 and of the end card (C13): LEXI against the naive baseline's ghost ledger, wrong commits, rupees and CELB. Every figure is a
fold over the audit stream and the finished calls, so a page that reloads gets the same figures, and none of them is typed in. The tally owns nothing
authoritative: `reset` only starts a new count."""

from collections import Counter, deque
from collections.abc import Mapping
from typing import Any

GHOST_KEPT = 40
SERIES_KEPT = 1500
LIVE_ENTRY = ("PREPARED", "DELIVERED", "POSTED")  # an entry that still moves the balance: not compensated, not rejected


class Tally:
    def __init__(self, alpha: Mapping[str, float], cap_inr: int, sign: Mapping[str, int]) -> None:
        self.alpha, self.cap, self.sign = alpha, cap_inr, sign
        self.reset()

    def reset(self) -> None:
        self.calls = 0
        self.lexi: Counter[str] = Counter()  # autonomous, wrong, escalated, human, human_wrong
        self.base: Counter[str] = Counter()  # commits, wrong
        self.inr: Counter[str] = Counter()  # lexi_posted, lexi_wrong, base_posted, base_wrong
        self.max_loss = {"lexi": 0, "base": 0}
        self.celb = {"cap": 0.0, "est": 0.0, "commits": 0}
        self.ghost: deque[dict[str, Any]] = deque(maxlen=GHOST_KEPT)
        self.intercepted = 0  # the naive baseline would have posted a wrong entry and LEXI did not
        self.intercepts: deque[dict[str, Any]] = deque(maxlen=8)
        self.series: deque[list[float]] = deque(maxlen=SERIES_KEPT)  # per finished call: n, LEXI wrong, baseline wrong, LEXI autonomous
        self.ghost_net: Counter[str] = Counter()
        self.entries: dict[str, tuple[str, str, str, int]] = {}  # entry_id -> (latest event, account/party, intent, amount)
        self.sessions: dict[str, str] = {}
        self.onset: float | None = None
        self.contained_at: float | None = None
        self.wrong_times: list[float] = []  # call time of every wrong autonomous commit

    # ---- feeds; each returns whether the figures changed
    def feed(self, kind: str, payload: Any) -> bool:
        if kind == "audit":
            return self.on_audit(payload)
        if kind == "call":
            return self.on_call(payload)
        if kind == "outcome":
            return self.on_outcome(payload)
        return False

    def on_audit(self, rec: Any) -> bool:
        p = rec.payload
        if rec.type == "SESSION_EVENT" and p["event"] == "OPEN":
            self.sessions[p["session_id"]] = p["account"]
        elif rec.type == "LEDGER_EVENT":
            account = self.sessions.get(p["session_id"], "")
            self.entries[p["entry_id"]] = (p["event"], f"{account}/{p['party_id']}", p["intent"], p["amount_inr"] or 0)
            return True
        elif rec.type == "SENTINEL_EVENT" and p["type"] == "STATE_CHANGE":
            if p.get("shift_onset_ts") is not None and self.onset is None:
                self.onset = p["shift_onset_ts"]
            if p["to_state"] == "REVOKED" and self.contained_at is None:
                self.contained_at = rec.ts
            return True
        elif rec.type == "DECISION" and p["mode"] == "LIVE":
            ref = p["decision"]["cert_ref"]
            if p["decision"]["kind"] == "COMMIT" and ref and not ref["hedged"]:  # 4.8: autonomous commits under a certified rung
                a = self.alpha.get(p["cell"], 0.0)
                amount = (p["sp"]["slots"].get("amount") or {}).get("top") or 0
                self.celb["cap"] += a * self.cap
                self.celb["est"] += a * amount
                self.celb["commits"] += 1
                return True
        return False

    def on_call(self, call: Mapping[str, Any]) -> bool:
        self.calls += 1
        if call["final"] == "COMMIT":
            self.lexi["autonomous"] += 1
            amount = int((call["values"] or {}).get("amount") or 0)
            self.inr["lexi_posted"] += amount
            if call["wrong"]:
                self.lexi["wrong"] += 1
                self.inr["lexi_wrong"] += amount
                self.max_loss["lexi"] = max(self.max_loss["lexi"], amount)
                self.wrong_times.append(call["ts"])
        else:
            self.lexi["escalated"] += 1
        if (b := call["baseline"]) is not None:
            v = b["values"]
            amount = int(v.get("amount") or 0)
            self.base["commits"] += 1
            self.inr["base_posted"] += amount
            if b["wrong"]:
                self.base["wrong"] += 1
                self.inr["base_wrong"] += amount
                self.max_loss["base"] = max(self.max_loss["base"], amount)
            key = f"{call['account']}/{v.get('party')}"
            self.ghost_net[key] += self.sign.get(v.get("intent"), 0) * amount
            lv = call["values"] or {}
            self.ghost.appendleft({"call_id": call["call_id"], "ts": call["ts"], "account": call["account"], "party_id": v.get("party"),
                                   "party": call["names"].get(v.get("party"), v.get("party")), "intent": v.get("intent"), "amount": amount,
                                   "date": v.get("date"), "wrong": b["wrong"], "lexi": call["final"],
                                   "lexi_amount": lv.get("amount"), "lexi_party": call["names"].get(lv.get("party"))})
            if b["wrong"] and not call["wrong"]:
                self.intercepted += 1
                self.intercepts.appendleft({"call_id": call["call_id"], "cell": call["cell"], "ghost": {**v, "party_name": call["names"].get(v.get("party"))},
                                            "lexi": call["values"], "lexi_party_name": call["names"].get((call["values"] or {}).get("party")),
                                            "final": call["final"], "probes": call["probes"]})
        self.series.append([self.calls, self.lexi["wrong"], self.base["wrong"], self.lexi["autonomous"]])
        return True

    def on_outcome(self, o: Any) -> bool:
        if o.autonomous:
            return False
        self.lexi["human"] += 1
        self.lexi["human_wrong"] += o.y_final
        return True

    # ---- the figures
    def snapshot(self) -> dict[str, Any]:
        lexi_net: Counter[str] = Counter()
        for event, key, intent, amount in self.entries.values():
            if event in LIVE_ENTRY:
                lexi_net[key] += self.sign.get(intent, 0) * amount
        n, commits = self.calls, self.lexi["autonomous"]
        late = [t for t in self.wrong_times if (self.onset is None or t >= self.onset) and (self.contained_at is None or t < self.contained_at)]
        return {"calls": n, "autonomous": commits, "escalated": self.lexi["escalated"], "human": self.lexi["human"], "human_wrong": self.lexi["human_wrong"],
                "autonomy_rate": commits / n if n else None, "wrong": self.lexi["wrong"], "wrong_rate": self.lexi["wrong"] / commits if commits else None,
                "baseline": {"commits": self.base["commits"], "wrong": self.base["wrong"], "wrong_rate": self.base["wrong"] / self.base["commits"] if self.base["commits"] else None,
                             "autonomy_rate": self.base["commits"] / n if n else None},
                "inr": {**self.inr, "max_loss_lexi": self.max_loss["lexi"], "max_loss_baseline": self.max_loss["base"]},
                "celb": {**self.celb, "cap_inr": self.cap}, "wrong_before_containment": len(late) if self.contained_at is not None else None,
                "contained": self.contained_at is not None, "ghost": list(self.ghost), "intercepted": self.intercepted, "intercepts": list(self.intercepts),
                "series": list(self.series),
                "net": {k: {"lexi": lexi_net.get(k, 0), "ghost": self.ghost_net.get(k, 0)} for k in sorted(set(lexi_net) | set(self.ghost_net))}}
