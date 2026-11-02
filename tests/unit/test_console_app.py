import json
from pathlib import Path
from types import SimpleNamespace as NS
from typing import Any

from fastapi.testclient import TestClient

from lexi.console.app import Hooks, create_app


def client(tmp_path: Path, tickets: list[Any] | None = None) -> tuple[TestClient, list[Any]]:
    seen: list[Any] = []

    async def human_commit(c: Any) -> Any:
        seen.append(c)
        return NS(status="POSTED", entry_id="e1")

    async def command(c: Any) -> dict[str, Any]:
        seen.append(c)
        return {"ok": True}

    async def twin() -> dict[str, Any]:
        return {"filling": False}

    hooks = Hooks(human_commit, seen.append, command, twin, lambda: {"registry": None, "tickets": tickets or [], "mode": "LIVE"}, tmp_path,
                  {"alpha": {}, "cap_inr": 25000, "sign": {"CREDIT": 1, "DEBIT": -1}})
    app, _ = create_app(hooks)
    return TestClient(app), seen


def test_state_page_and_static_files_are_served(tmp_path: Path) -> None:
    c, _ = client(tmp_path)
    s = c.get("/api/state").json()
    assert s["mode"] == "LIVE" and s["registry"] is None and s["tally"]["calls"] == 0
    assert "<title>" in c.get("/").text and c.get("/static/app.js").status_code == 200


def test_resolving_a_ticket_sends_a_human_commit_then_a_verdict_and_unknown_tickets_are_refused(tmp_path: Path) -> None:
    c, seen = client(tmp_path, [NS(ticket_id="t1", session_id="s1")])
    assert c.post("/api/tickets/nope/resolve", json={"intent": "CREDIT_ENTRY", "party_id": "P01"}).status_code == 404
    r = c.post("/api/tickets/t1/resolve", json={"intent": "CREDIT_ENTRY", "party_id": "P01", "amount_inr": 35000})
    assert r.json() == {"status": "POSTED", "entry_id": "e1"}
    assert [type(x).__name__ for x in seen] == ["HumanCommit", "HumanVerdict"] and seen[1].values == {"intent": "CREDIT_ENTRY", "party": "P01", "amount": 35000}


def test_reports_are_listed_newest_first_and_only_by_tag(tmp_path: Path) -> None:
    for tag, passed in (("a", True), ("b", False)):
        (tmp_path / tag).mkdir()
        (tmp_path / tag / "report.json").write_text(json.dumps({"passed": passed}), encoding="utf-8")
    c, _ = client(tmp_path)
    assert {r["tag"]: r["passed"] for r in c.get("/api/reports").json()} == {"a": True, "b": False}
    assert c.get("/api/reports/a").json() == {"passed": True}
    assert c.get("/api/reports/missing").status_code == 404


def test_the_twin_command_is_passed_through_and_the_tally_can_be_reset(tmp_path: Path) -> None:
    c, seen = client(tmp_path)
    assert c.post("/api/twin/command", json={"action": "START", "scenario": "S0", "params": {"rate": 60}}).json() == {"ok": True}
    assert seen[0].action == "START" and c.post("/api/tally/reset").json() == {"ok": True}
