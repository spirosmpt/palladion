from datetime import timedelta

import pytest

from palladion.cases.service import CaseService, Status
from palladion.engines.registry.registry import Registry
from palladion.field.apply import outcome
from palladion.field.extract import check, read, rules
from palladion.field.service import FieldError, FieldService
from palladion.field.simulator import CrewSimulator
from palladion.llm.client import LLM, ResponseCache, ScriptedProvider
from palladion.pipeline.decide import Decision, Outcome
from palladion.pipeline.locate import Location
from palladion.pipeline.process import Message, Processed

from .conftest import T0


def kinds(text):
    return {(f.kind, f.value) for f in rules(text)}


# -- reading ------------------------------------------------------------------------------------------
def test_rules_read_counts_in_digits_and_words_and_statuses():
    assert kinds("Πήραμε 2 άτομα. Επιστρέφουμε.") == {("rescued_n", 2), ("returning", True)}
    assert kinds("Απεγκλωβίσαμε τρεις, μένουν ακόμα δύο.") == {("rescued_n", 3), ("remaining_n", 2)}
    assert ("not_found", True) in kinds("Δεν βρήκαμε κανέναν στη διεύθυνση.")
    assert kinds("Βλάβη στη μηχανή.") == {("failed", True)}


def test_negated_uncertain_and_questions_are_not_findings():
    assert kinds("Δεν πήραμε κανέναν ακόμα, ίσως μένουν 2 μέσα.") == set()
    assert kinds("Πήραμε 3;") == set()
    assert kinds("Αν φτάσαμε, θα πούμε.") == set()


def test_agent_findings_are_checked_against_the_report():
    text = "Πήραμε 2 άτομα, μένει ακόμα 1 στο σπίτι."
    assert check("rescued_n", "Πήραμε 2 άτομα", "2", text)[0] is not None
    assert check("rescued_n", "Πήραμε 3 άτομα", "3", text)[0] is None              # not in the report
    assert check("remaining_n", "μένει ακόμα 1", "2", text)[0] is None              # not the number written
    assert check("rescued_n", "2 άτομα", "2", text)[0] is None                      # no verb: not a rescue
    assert check("all_out", "Δεν βγήκαν όλοι", None, "Δεν βγήκαν όλοι.")[0] is None  # negated


def test_agent_adds_only_checked_findings(tmp_path):
    answer = ('{"findings": [{"kind": "rescued_n", "quote": "βγάλαμε την κυρία και τον γιο", "count_quote": "2"},'
              ' {"kind": "new_hazard", "quote": "αναζωπύρωση πίσω από το σπίτι"},'
              ' {"kind": "remaining_n", "quote": "μένουν 4", "count_quote": "4"}]}')
    llm = LLM(ScriptedProvider(lambda s, u, t: answer), ResponseCache(tmp_path / "c.jsonl"), None, tmp_path / "t.jsonl")
    r = read(llm, "Βγάλαμε την κυρία και τον γιο. Υπάρχει αναζωπύρωση πίσω από το σπίτι.")
    assert r.check == "agent_checked"
    assert {(f.kind, f.source) for f in r.findings} == {("new_hazard", "rules")}
    assert len(r.rejected) == 2


# -- what it means for the case ----------------------------------------------------------------------
def test_outcomes():
    assert outcome(rules("Πήραμε 2 άτομα."), 2).status == "resolved"
    partial = outcome(rules("Πήραμε 1 άτομο. Μένει ακόμα 1."), 2)
    assert partial.status == "partial" and partial.remaining == 1 and partial.decision == "DISPATCH_UNIT_CANDIDATE"
    assert outcome(rules("Δεν βρήκαμε κανέναν."), 2).decision == "CALLBACK"
    assert outcome(rules("Είχαν φύγει ήδη με γείτονα."), 2).status == "resolved"
    assert outcome(rules("Πήραμε 1 άτομο."), None).status == "review"          # did anyone stay? we do not know
    assert outcome(rules("Πήραμε 5 άτομα."), 2).status == "review"             # more than we knew of
    hazard = outcome(rules("Μένουν 2. Αναζωπύρωση στον δρόμο."), 3)
    assert hazard.decision == "REVIEW_REQUIRED" and hazard.open_items
    assert outcome(rules("Φύγαμε από τη βάση."), 2).status is None             # a status says nothing of people


# -- the case and the crew simulator ----------------------------------------------------------------------
def triage(m: Message) -> Processed:
    return Processed(m, None, Location("resolved", 23.98, 38.05, 200, "Κυκλάδων, Μάτι"), 30,
                     Outcome(Decision.DISPATCH_UNIT_CANDIDATE, 80, ["r"]))


class Mission:
    def __init__(self, id_, case, unit, minutes):
        self.id, self.case_id, self.unit = id_, case, unit
        self.depart = T0 + timedelta(minutes=minutes)
        self.arrive, self.leave_scene, self.back = (self.depart + timedelta(minutes=k) for k in (8, 20, 28))


def desk(tmp_path, people=3):
    reg = Registry(tmp_path / "ev.jsonl")
    cases = CaseService([Message(id="m1", time=T0, channel="sms", text="x")], triage, reg)
    cases.tick(T0, sync=True)
    cases.act("m1", "approve", "anna", T0)
    f = FieldService(reg, None, case_people=lambda cid: people, on_outcome=cases.apply_field)
    return reg, cases, f


def test_simulated_crew_closes_the_case_and_it_survives_restart(tmp_path):
    reg, cases, f = desk(tmp_path, people=3)
    m = Mission("m1-1", "m1", "ΠΥ-01", 1)
    f.assign("ΠΥ-01", m.id, T0, case="m1")
    sim = CrewSimulator(f, {"m1": "Απεγκλωβίσαμε 3 άτομα, βγήκαν όλοι."}, land=lambda: [m])
    sim.tick(m.arrive)
    assert f.state("ΠΥ-01", m.arrive)["state"] == "on_scene" and cases.get("m1").status == Status.approved
    sim.tick(m.back)
    assert cases.get("m1").status == Status.resolved
    assert f.state("ΠΥ-01", m.back)["state"] == "available"
    assert all(r.simulated for r in f.reports)
    again = CaseService([Message(id="m1", time=T0, channel="sms", text="x")], triage, reg)
    again.tick(T0, sync=True)
    assert again.get("m1").status == Status.resolved and again.get("m1").field_people == 0


def test_people_left_behind_go_back_to_the_queue(tmp_path):
    _, cases, f = desk(tmp_path, people=3)
    f.assign("ΠΥ-01", "m1-1", T0, case="m1")
    f.report("ΠΥ-01", "Πήραμε 2 άτομα, μένει ακόμα 1, δεν θέλει να φύγει.", T0 + timedelta(minutes=15),
             source="operator", actor="anna")
    c = cases.get("m1")
    assert c.status == Status.proposed and c.final_decision is None and c.field_people == 1
    assert c.processed.outcome.reasons[0].startswith("Αναφορά πεδίου")


def test_without_a_record_the_unit_waits_on_scene_and_reports_need_a_text_or_a_button(tmp_path):
    _, cases, f = desk(tmp_path)
    m = Mission("m1-1", "m1", "ΠΥ-01", 1)
    f.assign("ΠΥ-01", m.id, T0, case="m1")
    CrewSimulator(f, {}, land=lambda: [m]).tick(m.back + timedelta(hours=1))
    assert f.state("ΠΥ-01", m.back + timedelta(hours=1))["state"] == "on_scene"
    with pytest.raises(FieldError):
        f.report("ΠΥ-01", "", m.back)
    with pytest.raises(FieldError):
        f.report("ΠΥ-01", "", m.back, source="crew", button="available")    # only an operator presses buttons


def test_a_report_about_another_mission_is_kept_but_not_applied(tmp_path):
    _, cases, f = desk(tmp_path)
    f.assign("ΠΥ-01", "m1-2", T0, case="m1")
    r = f.report("ΠΥ-01", "Πήραμε 3 άτομα.", T0 + timedelta(minutes=5), mission="m1-1")
    assert not r.applied and r.note and cases.get("m1").status == Status.approved
