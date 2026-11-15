import json
from datetime import timedelta

from palladion.agents.location import SYSTEM, LocationTools, needs_agent, run_location
from palladion.llm.client import LLM, ResponseCache, ScriptedProvider
from palladion.pipeline.locate import Location, locate

from .conftest import T0
from .test_agents import gaz, intake

BBOX = (23.90, 38.00, 24.05, 38.10)
TEXT = "Είμαστε στην Κολοκοτρώνη, ψηλά πάνω από τον Βουτζά, βλέπουμε φλόγες."


class FakeFire:
    t0 = T0

    def arrival_time(self, lon, lat):
        return T0 + timedelta(minutes=30) if lat < 38.06 else None


def llm_with(turns, tmp_path):
    it = iter(turns)

    def answer(system, user, tier):
        assert system.startswith(SYSTEM)
        return json.dumps(next(it), ensure_ascii=False)
    p = ScriptedProvider(answer)
    return LLM(p, ResponseCache(tmp_path / "c.jsonl"), None, tmp_path / "t.jsonl"), p


def ambiguous():
    out = intake([{"kind": "street", "name": "Κολοκοτρώνη", "quote": "Κολοκοτρώνη"}])
    loc = locate(out, gaz(), BBOX)
    assert loc.status == "ambiguous" and needs_agent(loc, True) == "ambiguous"
    assert needs_agent(Location("approximate", 24.0, 38.0, 900, "ακτή, Μάτι"), False) is None    # beach alone
    return loc


def test_agent_chooses_with_tools_and_code_checks(tmp_path):
    loc = ambiguous()
    turns = [{"call": {"tool": "search_street", "args": {"name": "Κολοκοτρώνη"}, "why_quote": "στην Κολοκοτρώνη"}},
             {"answer": {"candidate_id": "s2", "evidence": ["στην Κολοκοτρώνη", "πάνω από τον Βουτζά"],
                         "reason_el": "Πάνω από τον Βουτζά."}}]
    llm, p = llm_with(turns, tmp_path)
    g = gaz()
    tools = LocationTools(g, FakeFire(), T0, BBOX, [g.find_area("Νέος Βουτζάς")])
    got, rv = run_location(llm, TEXT, tools, loc, "ambiguous")
    near = {k: tools.seen[k]["m_from_areas_in_message"].get("Νέος Βουτζάς", 10**6) for k in ("s1", "s2")}
    assert near["s2"] < near["s1"]
    assert rv.accepted and got.status == "resolved" and "Νέος Βουτζάς" in got.label
    # "Βουτζά" is not the full name "Νέος Βουτζάς": the area was inferred, so the operator confirms it.
    assert "area_inferred" in got.flags and got.question_el
    assert p.calls == 2 and rv.steps[0]["results"] == 2
    assert tools.seen["s1"]["area"] == "Νέα Μάκρη"                       # the other stretch, not chosen


def test_invented_id_or_quote_is_refused(tmp_path):
    loc = ambiguous()
    tools = LocationTools(gaz(), FakeFire(), T0, BBOX)
    llm, _ = llm_with([{"answer": {"candidate_id": "s9", "evidence": ["στην Κολοκοτρώνη"]}}], tmp_path / "a")
    got, rv = run_location(llm, TEXT, tools, loc, "ambiguous")
    assert got is loc and not rv.accepted and "not returned" in rv.reason

    turns = [{"call": {"tool": "search_street", "args": {"name": "Κολοκοτρώνη"}}},
             {"answer": {"candidate_id": "s1", "evidence": ["στη Νέα Μάκρη"]}}]          # not in the message
    llm, _ = llm_with(turns, tmp_path / "b")
    got, rv = run_location(llm, TEXT, LocationTools(gaz(), FakeFire(), T0, BBOX), loc, "ambiguous")
    assert got is loc and not rv.accepted


def test_steps_are_bounded_and_corner_tool(tmp_path):
    loc = Location("none")
    turns = [{"call": {"tool": "search_street", "args": {"name": "Μεσσηνίας"}}},
             {"call": {"tool": "search_street", "args": {"name": "Ζακύνθου"}}},
             {"call": {"tool": "corner", "args": {"a": "s1", "b": "s2"}}},
             {"call": {"tool": "place_info", "args": {"id": "s3"}}},
             {"call": {"tool": "search_area", "args": {"name": "Μάτι"}}},              # one too many: forced answer
             ]
    llm, p = llm_with(turns, tmp_path)
    tools = LocationTools(gaz(), FakeFire(), T0, BBOX)
    got, rv = run_location(llm, "Γωνία Μεσσηνίας και Ζακύνθου!", tools, loc, "not_found")
    assert p.calls == 5 and len(rv.steps) == 4 and got is loc          # no answer: the code result stays
    assert any(r["kind"] == "corner" for r in tools.seen.values())


def resolved_kolokotroni_voutzas():
    return Location("resolved", 23.983, 38.050, 400, "Κολοκοτρώνη, Νέος Βουτζάς", "Νέος Βουτζάς")


def test_check_mode_confirms_the_code_place(tmp_path):
    loc = resolved_kolokotroni_voutzas()
    assert needs_agent(loc, True, has_place=True) == "check"
    assert needs_agent(Location("out_of_area", 23.7, 37.9, 100, "Αθήνα"), False, has_place=True) is None
    llm, _ = llm_with([{"answer": {"candidate_id": "c0", "evidence": ["στην Κολοκοτρώνη"]}}], tmp_path)
    tools = LocationTools(gaz(), FakeFire(), T0, BBOX)
    got, rv = run_location(llm, TEXT, tools, loc, "check")
    assert got is loc and rv.outcome == "confirmed" and not rv.accepted and tools.seen["c0"]["status"] == "resolved"


def test_check_mode_refines_an_area_and_disputes_without_moving(tmp_path):
    text = "Είμαστε στη Μεσσηνίας στο Μάτι, βοήθεια"
    area = Location("approximate", 23.992, 38.049, 1500, "Μάτι", "Μάτι")
    turns = [{"call": {"tool": "search_street", "args": {"name": "Μεσσηνίας"}}},
             {"answer": {"candidate_id": "s2", "evidence": ["στη Μεσσηνίας στο Μάτι"]}}]
    llm, _ = llm_with(turns, tmp_path / "a")
    got, rv = run_location(llm, text, LocationTools(gaz(), FakeFire(), T0, BBOX), area, "check")
    assert rv.outcome == "refined" and rv.accepted and got.status == "resolved" and got.label == "Μεσσηνίας, Μάτι"

    code = Location("resolved", 23.965, 38.083, 100, "Κολοκοτρώνη, Νέα Μάκρη", "Νέα Μάκρη")
    turns = [{"call": {"tool": "search_street", "args": {"name": "Κολοκοτρώνη"}}},
             {"answer": {"candidate_id": "s3", "evidence": ["στην Κολοκοτρώνη", "πάνω από τον Βουτζά"]}}]
    llm, _ = llm_with(turns, tmp_path / "b")
    g = gaz()
    got, rv = run_location(llm, TEXT, LocationTools(g, FakeFire(), T0, BBOX, [g.find_area("Νέος Βουτζάς")]), code,
                           "check")
    assert got is code and rv.outcome == "disputed" and "location_disputed" in got.flags
    assert rv.proposed == "Κολοκοτρώνη, Νέος Βουτζάς" and not rv.accepted


def test_earlier_messages_and_place_info_and_where_they_left(tmp_path):
    class Shore:
        def nearest_shore(self, lon, lat):
            from palladion.engines.coast import Shore as S
            return S.cliff, 84.0

    def earlier(name):
        hit = {"case": "m21", "name": "Κολοκοτρώνη", "area": "Νέος Βουτζάς", "status": "resolved",
               "lon": 23.983, "lat": 38.050, "extent_m": 400, "minutes_before": 40}
        return [hit] if "κολοκοτ" in name.lower() else []

    text = "Φύγαμε από την Ταϋγέτου, τώρα είμαστε στα βράχια στην Κολοκοτρώνη, δεν ήρθε κανείς"
    turns = [{"call": {"tool": "earlier_messages", "args": {"name": "Κολοκοτρώνη"}}},
             {"call": {"tool": "place_info", "args": {"id": "e1"}}},
             {"call": {"tool": "search_street", "args": {"name": "Ταϋγέτου"}}},
             {"answer": {"candidate_id": "e1", "evidence": ["στα βράχια στην Κολοκοτρώνη"], "left_from_id": "s2"}}]
    llm, _ = llm_with(turns, tmp_path)
    tools = LocationTools(gaz(), FakeFire(), T0, BBOX, coast=Shore(), earlier=earlier)
    got, rv = run_location(llm, text, tools, ambiguous(), "ambiguous")
    assert rv.accepted and got.label == "Κολοκοτρώνη, Νέος Βουτζάς" and "area_inferred" in got.flags
    assert "m21" in got.question_el and rv.left_from == "Ταϋγέτου, Νέος Βουτζάς"
    from palladion.agents.location import ToolCall
    assert rv.steps[1]["tool"] == "place_info"
    assert tools.run(ToolCall(tool="place_info", args={"id": "e1"}))["shore"] == {"kind": "cliff", "m": 80}


def test_a_guess_among_same_name_streets_does_not_move_people(tmp_path):
    loc = ambiguous()
    text = "Στην Κολοκοτρώνη, ο δρόμος τελειώνει στο δάσος, δεν έχουμε από πού να βγούμε!"
    turns = [{"call": {"tool": "search_street", "args": {"name": "Κολοκοτρώνη"}}},
             {"answer": {"candidate_id": "s1", "evidence": ["Στην Κολοκοτρώνη", "τελειώνει στο δάσος"]}}]
    llm, _ = llm_with(turns, tmp_path)
    got, rv = run_location(llm, text, LocationTools(gaz(), FakeFire(), T0, BBOX), loc, "ambiguous")
    assert got is loc and got.status == "ambiguous" and rv.outcome == "guess" and not rv.accepted
    assert rv.proposed and "Κολοκοτρώνη" in rv.proposed


def test_the_shore_tells_same_name_streets_apart(tmp_path):
    class Shore:
        def nearest_shore(self, lon, lat):
            from palladion.engines.coast import Shore as S
            return (S.cliff, 120.0) if lat < 38.06 else (S.beach, 3000.0)

    loc = ambiguous()
    text = "Είμαστε στα βράχια στην Κολοκοτρώνη, δεν μπορούμε να ανέβουμε"
    turns = [{"call": {"tool": "search_street", "args": {"name": "Κολοκοτρώνη"}}},
             {"answer": {"candidate_id": "s2", "evidence": ["στα βράχια στην Κολοκοτρώνη"]}}]
    llm, _ = llm_with(turns, tmp_path)
    got, rv = run_location(llm, text, LocationTools(gaz(), FakeFire(), T0, BBOX, coast=Shore()), loc, "ambiguous")
    assert rv.accepted and rv.outcome == "found" and got.area == "Νέος Βουτζάς" and "area_inferred" in got.flags
    assert "shore" in rv.reason
