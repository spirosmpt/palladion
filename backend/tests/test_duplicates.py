import json
from datetime import timedelta

import pytest

from palladion.agents.duplicates import DuplicateResult, Relation, run_duplicates
from palladion.agents.intake import IntakeResult, Place, PlaceKind, PlaceRole
from palladion.cases.service import CaseError, CaseService, Status
from palladion.engines.registry.registry import Registry
from palladion.llm.client import LLM, ResponseCache, ScriptedProvider
from palladion.pipeline.decide import Decision, Outcome
from palladion.pipeline.dedupe import candidates
from palladion.pipeline.locate import Location
from palladion.pipeline.process import Message, Processed

from .conftest import T0
from .test_agents import intake

EARLIER = [("m06", "17:40", "199_call", "Είμαστε τέσσερις στην Κυανής Ακτής, η γιαγιά δεν περπατάει."),
           ("m07", "17:42", "sms", "Καπνός στη Ραφήνα.")]
NEW = ("m24", "facebook", "Η οικογένεια στην Κυανής Ακτής με τη γιαγιά ακόμα περιμένει!")


def llm_answering(answer: dict, tmp_path) -> LLM:
    return LLM(ScriptedProvider(lambda s, u, t: json.dumps(answer, ensure_ascii=False)),
               ResponseCache(tmp_path / "c.jsonl"), None, tmp_path / "t.jsonl")


def test_agent_match_needs_both_quotes(tmp_path):
    good = {"match_id": "m06", "relation": "same_incident", "new_quote": "με τη γιαγιά",
            "earlier_quote": "η γιαγιά δεν περπατάει", "reason_el": "Ίδια οικογένεια."}
    r = run_duplicates(llm_answering(good, tmp_path / "a"), NEW, EARLIER)
    assert r.match_id == "m06" and r.evidence["earlier"] == "η γιαγιά δεν περπατάει"

    invented = {**good, "earlier_quote": "ο παππούς είναι στο κρεβάτι"}
    r = run_duplicates(llm_answering(invented, tmp_path / "b"), NEW, EARLIER)
    assert r.match_id is None and r.rejected

    unknown_id = {**good, "match_id": "m99"}
    assert run_duplicates(llm_answering(unknown_id, tmp_path / "c"), NEW, EARLIER).match_id is None

    other = {"match_id": None, "relation": "same_place_other_people", "reason_el": "Άλλη οικογένεια."}
    r = run_duplicates(llm_answering(other, tmp_path / "d"), NEW, EARLIER)
    assert r.match_id is None and r.relation == Relation.same_place_other_people and not r.rejected


def proc(id_, minutes, places, loc=None, refers=False, channel="sms"):
    m = Message(id=id_, time=T0 + timedelta(minutes=minutes), channel=channel, text="x")
    out = intake(places, refers_to_earlier_report=refers)
    return Processed(m, IntakeResult(output=out), loc or Location("none"), None,
                     Outcome(Decision.EVACUATE_ROUTE_CANDIDATE, 60, ["r"]))


def street(name, role=PlaceRole.person):
    return Place(kind=PlaceKind.street, name=name, quote=name, role=role)


def test_candidates_blocking_rules():
    is_area = {"Μάτι", "Mati"}.__contains__
    a = proc("a", 0, [street("Κυανής Ακτής"), Place(kind=PlaceKind.area, name="Μάτι", quote="Μάτι")])
    town_only = proc("t", 5, [Place(kind=PlaceKind.area, name="Mati", quote="Mati")])
    greeklish = proc("g", 10, [street("kyanis aktis")])
    fire_only = proc("f", 12, [street("Κυανής Ακτής", role=PlaceRole.fire)])
    too_late = proc("late", 200, [street("Κυανής Ακτής")])
    earlier = [a, town_only]
    assert candidates(town_only, [a], is_area) == []             # a shared town name is not enough
    assert [c.id for c in candidates(greeklish, earlier, is_area)] == ["a"]
    assert candidates(fire_only, earlier, is_area) == []         # where the fire is, not where people are
    assert candidates(too_late, earlier, is_area) == []          # outside the two-hour window

    near1 = proc("n1", 0, [], Location("resolved", 23.9900, 38.0500, 30, "x"))
    near2 = proc("n2", 5, [], Location("resolved", 23.9920, 38.0505, 30, "y"))      # ~180 m
    far = proc("n3", 6, [], Location("resolved", 24.0100, 38.0500, 30, "z"))        # ~1.7 km
    assert [c.id for c in candidates(near2, [near1], is_area)] == ["n1"]
    assert candidates(far, [near1], is_area) == []

    first = proc("p", 0, [], channel="199_call")
    again = proc("q", 20, [], refers=True, channel="199_call")
    assert [c.id for c in candidates(again, [first], is_area)] == ["p"]


def svc_with_dup(tmp_path, front):
    msgs = [Message(id=i, time=T0 + timedelta(minutes=t), channel="sms", text=i) for i, t in
            [("a", 0), ("b", 10), ("c", 20)]]

    def triage(m):
        return Processed(m, None, Location("resolved", 23.98, 38.05, 50, "Οδός"), front.get(m.id),
                         Outcome(Decision.EVACUATE_ROUTE_CANDIDATE, 60, ["r"]))

    def dedupe(p, earlier):
        if p.message.id in ("b", "c") and any(e.message.id == "a" for e in earlier):
            return DuplicateResult(match_id="a", relation=Relation.same_incident, reason_el="ίδιοι",
                                   evidence={"new": "b", "earlier": "a"})
        return None
    return CaseService(msgs, triage, Registry(tmp_path / "ev.jsonl"), dedupe=dedupe), dedupe, triage, msgs


def test_merge_takes_earliest_deadline_reopens_and_survives_restart(tmp_path):
    svc, dedupe, triage, msgs = svc_with_dup(tmp_path, {"a": 60, "b": 15, "c": 40})
    now = T0 + timedelta(minutes=12)
    svc.tick(now, sync=True)
    svc.act("a", "approve", "op1", now)
    assert svc.get("b").duplicate.match_id == "a"
    with pytest.raises(CaseError):
        svc.act("a", "not_duplicate", "op1", now)                # a has no suggestion

    svc.act("b", "merge", "op1", now)
    a, b = svc.get("a"), svc.get("b")
    assert b.status == Status.merged and b.merged_into == "a" and a.linked == ["b"]
    assert a.status == Status.proposed and a.reopened_by == "b"            # handled case reopened
    assert a.fire_arrival == T0 + timedelta(minutes=25)                      # b's deadline is nearer
    with pytest.raises(CaseError):
        svc.act("b", "approve", "op1", now)                                  # act on the primary instead
    assert [c.message.id for c in svc.visible(now)][-1] == "b"

    svc.act("a", "approve", "op1", now)                                      # approved again after the merge
    assert svc.get("a").reopened_by is None

    again = CaseService(msgs, triage, Registry(tmp_path / "ev.jsonl"), dedupe=dedupe)
    again.tick(now, sync=True)
    a2, b2 = again.get("a"), again.get("b")
    assert b2.status == Status.merged and a2.linked == ["b"] and a2.status == Status.approved
    assert a2.fire_arrival == T0 + timedelta(minutes=25)

    again.act("b", "unmerge", "op2", now)
    assert again.get("b").status == Status.proposed and again.get("a").linked == []
    assert again.get("a").fire_arrival == T0 + timedelta(minutes=60)


def test_dismissed_suggestion_and_merge_into_root(tmp_path):
    svc, *_ = svc_with_dup(tmp_path, {"a": 60, "b": 50, "c": 40})
    now = T0 + timedelta(minutes=25)
    svc.tick(now, sync=True)
    svc.act("b", "not_duplicate", "op1", now)
    assert svc.get("b").duplicate_dismissed
    svc.act("b", "merge", "op1", now, target="a")       # the operator can still merge by hand
    svc.act("c", "merge", "op1", now, target="b")       # b is merged: c goes to the root, a
    assert svc.get("c").merged_into == "a" and svc.get("a").linked == ["b", "c"]
    types = [e.type for e in svc.registry.events("case:a")]
    assert types.count("case.linked") == 2
