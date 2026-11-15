import json
from datetime import timedelta
from types import SimpleNamespace as NS

import pytest

from palladion.agents.coastguard import draft
from palladion.coastguard.service import CoastGuardError, CoastGuardService
from palladion.engines.registry.registry import Actor, ActorKind, Forbidden, Registry
from palladion.llm.client import LLM, ResponseCache, ScriptedProvider

from .conftest import T0

NOW = T0 + timedelta(minutes=80)


def case(cid, minute, lon, lat, people=None, decision="SEA_RESCUE_CANDIDATE", situation="sea", front=10):
    o = NS(situation=NS(kind=NS(value=situation), quote="στα βράχια"), vulnerable=[])
    p = NS(location=NS(lon=lon, lat=lat, label="ακτή, Μάτι", status="approximate"), intake=NS(output=o),
           outcome=NS(decision=NS(value=decision)))
    return NS(message=NS(id=cid, time=T0 + timedelta(minutes=minute)), processed=p, status=NS(value="proposed"),
              final_decision=None, fire_arrival=NOW + timedelta(minutes=front), people=people)


class Cases:
    def __init__(self, cs):
        self.cs = cs

    def visible(self, now):
        return [c for c in self.cs if c.message.time <= now]

    def people(self, cid):
        return next(c.people for c in self.cs if c.message.id == cid)


class Coast:
    def nearest_shore(self, lon, lat):
        from palladion.engines.coast import Shore
        return Shore.cliff, 60.0

    def nearest_access(self, lon, lat):
        from palladion.engines.coast import Access, Shore
        return Access(Shore.beach, "Παραλία Μάτι", lon, lat, 300.0)

    def near_cliff(self, lon, lat, within_m=150.0):
        return True


def desk(tmp_path, cs, drafter=None):
    reg = Registry(tmp_path / "ev.jsonl")
    svc = CoastGuardService(lambda: reg, lambda: Cases(cs), lambda: None, Coast(), drafter)
    return reg, svc


CASES = [case("m19", 50, 23.999, 38.045, people=None, front=5),
         case("m20", 51, 23.9995, 38.0452, people=6, front=4),
         case("m28", 60, 24.004, 38.032, people=3, front=-2),                  # another stretch, 1.5 km away
         case("m05", 40, 23.98, 38.05, people=2, decision="DISPATCH_UNIT_CANDIDATE", situation="home")]


def test_groups_by_shore_and_template(tmp_path):
    _, svc = desk(tmp_path, CASES)
    g = svc.groups(NOW)
    assert [v["case_ids"] for v in g.values()] == [["m19", "m20"], ["m28"]]       # m05 is not a sea case
    assert g["G1"]["label"] == "Παραλία Μάτι" and g["G1"]["front_min"] == 4
    assert svc.code_order(g) == ["G2", "G1"]                                      # the front has passed G2: first
    text = svc.template(NOW, g)("G1")
    assert "m19, m20" in text and "δηλωμένα άτομα 6" in text and "Γκρεμός" in text


def llm_with(turns, tmp_path):
    it = iter(turns)
    p = ScriptedProvider(lambda s, u, t: json.dumps(next(it), ensure_ascii=False))
    return LLM(p, ResponseCache(tmp_path / "c.jsonl"), None, tmp_path / "t.jsonl"), p


def test_the_agent_drafts_with_tools_and_code_checks_every_number(tmp_path):
    _, svc = desk(tmp_path, CASES)
    g = svc.groups(NOW)
    turns = [{"call": {"tool": "group_cases", "group": "G1"}},
             {"call": {"tool": "fire", "group": "G1"}},
             {"call": {"tool": "shore", "group": "G1"}},
             {"answer": {"requests": [
                 {"group": "G1", "cites": ["m19", "m20"], "why_el": "λιγότερος χρόνος",
                  "text_el": "Παραλία Μάτι, γκρεμός στα 60 m: πρόσβαση μόνο με σκάφος. Τουλάχιστον 6 άτομα στα "
                             "βράχια (m19, m20). Το μέτωπο φτάνει σε 4′."},
                 {"group": "G2", "cites": ["m28"], "text_el": "Κόκκινο Λιμανάκι: 40 άτομα στο νερό, ελάτε αμέσως."}]}}]
    llm, p = llm_with(turns, tmp_path)
    out = draft(llm, g, svc.facts(NOW, g), svc.template(NOW, g), svc.code_order(g))
    assert p.calls == 4 and [s["tool"] for s in out["steps"]] == ["group_cases", "fire", "shore"]
    assert out["requests"]["G1"]["source"] == "agent" and "πρόσβαση μόνο με σκάφος" in out["requests"]["G1"]["text"]
    # G2: never looked up, and "40" is in no tool result -> the template goes out instead
    assert out["requests"]["G2"]["source"] == "template" and out["status"] == "agent_partial"
    assert any("40" in str(r["problems"]) for r in out["rejected"])
    assert out["order"] == ["G1", "G2"] and out["order_by"] == "agent"


def test_drafts_are_logged_once_and_sending_is_a_persons_act(tmp_path):
    calls = []

    def drafter(groups, facts, template, order):
        calls.append(1)
        return {"requests": {k: {"text": template(k), "cites": v["case_ids"], "why": "", "source": "template"}
                             for k, v in groups.items()}, "order": order, "status": "agent_failed", "steps": [],
                "calls": [], "rejected": [], "order_by": "code", "error": "x"}

    reg, svc = desk(tmp_path, CASES, drafter)
    v = svc.view(NOW)
    assert v["pending"] and not calls                                     # not asked: nothing runs
    svc.draft_now(NOW)
    svc.draft_now(NOW)
    assert len([e for e in reg.events("coastguard") if e.type == "coastguard.drafted"]) == 1
    v = svc.view(NOW, ask=True)
    assert not v["pending"] and v["groups"][0]["id"] == "G2" and v["waiting_since"]
    after = svc.send("G1", v["groups"][1]["request"]["text"], "anna", NOW)
    g1 = next(g for g in after["groups"] if g["id"] == "G1")
    assert g1["sent"]["by"] == "anna" and g1["new_since_sent"] == []
    assert after["waiting_since"].startswith((T0 + timedelta(minutes=60)).isoformat()[:16])   # m28 still waits
    with pytest.raises(CoastGuardError):
        svc.send("G9", "κείμενο αιτήματος", "anna", NOW)
    with pytest.raises(Forbidden):
        reg.append("coastguard", "coastguard.request_sent", Actor(kind=ActorKind.agent, id="x"), {},
                   expected_version=reg.version("coastguard"))


def test_supervisor_raises_the_sea_waiting_without_a_request():
    from palladion.supervisor import detectors as D
    snap = {"cases": [], "missions": [], "units": [], "sea": {"waiting_since": NOW - timedelta(minutes=12), "cases": 3}}
    [f] = D.coast_guard(snap, NOW, D.Config())
    assert f["allowed"][0] == "open_coastguard" and "12′" in f["text_el"]
    snap["sea"]["waiting_since"] = NOW - timedelta(minutes=5)
    assert D.coast_guard(snap, NOW, D.Config()) == []
