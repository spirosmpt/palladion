"""Run the development set and compare with the expected answers.

    python -m palladion.eval.run_dev               # all 40 messages with Gemini (cached answers are free)
    python -m palladion.eval.run_dev --n 10        # the first 10 only
    python -m palladion.eval.run_dev --oracle      # skip the model: intake built from the gold file,
                                                   # to test location + decision code on their own

Writes runtime/eval/dev_<provider>.json and prints a table.
"""
import argparse
import json
import sys
from pathlib import Path

import yaml

from palladion.agents.intake import (
    IntakeOutput,
    IntakeResult,
    MessageKind,
    People,
    Place,
    PlaceKind,
    Reporter,
    SituationQ,
    Vulnerable,
)
from palladion.api.state import build_state
from palladion.config import REPO_ROOT, get_settings
from palladion.geo.gazetteer import Gazetteer
from palladion.geo.proj import haversine_m
from palladion.llm.client import BudgetExhausted, build_llm
from palladion.pipeline.dedupe import candidates, find_duplicate
from palladion.pipeline.process import Message, Processed, finish, load_messages, process

GOLD = REPO_ROOT / "eval" / "dev" / "mati_2018_gold.yaml"
KIND_FOR = {"REPORTED_SAFE": MessageKind.safety_report, "MISSING_PERSON": MessageKind.missing_person,
            "INFO": MessageKind.situational_info, "MONITOR": MessageKind.question_no_danger}


def oracle_intake(text: str, g: dict) -> IntakeResult:
    """An intake result made from the expected answers: what a perfect intake agent would return."""
    first = g["decision"][0]
    flags = g.get("flags", [])
    kind = (MessageKind.instruction_to_system if "injection" in flags else
            MessageKind.unverified_claim if "rumor" in flags else KIND_FOR.get(first, MessageKind.help_request))
    loc = g.get("location") or {}
    places = []
    if loc.get("street"):
        places.append(Place(kind=PlaceKind.street, name=loc["street"], quote=loc["street"]))
    if loc.get("area"):
        places.append(Place(kind=PlaceKind.area, name=loc["area"], quote=loc["area"]))
    out = IntakeOutput(
        kind=kind, reporter=Reporter(g.get("reporter", "self")), language="el",
        people=People(count=g["people"], quote=str(g["people"])) if g.get("people") else None,
        vulnerable=[Vulnerable(kind=v, quote=v) for v in g.get("vulnerable", [])],
        situation=SituationQ(kind=g.get("situation", "unknown"), quote="-"),
        places=places, hazards=[], asks_for=[], refers_to_earlier_report=bool(g.get("duplicate_of")),
        summary_el="(oracle)")
    return IntakeResult(output=out, injection_suspected="injection" in flags)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--n", type=int, default=None, help="only the first N messages (by time)")
    ap.add_argument("--oracle", action="store_true", help="use the gold answers instead of the model")
    args = ap.parse_args(argv)

    settings = get_settings()
    state = build_state(settings)
    spec = state.scenario.spec
    gaz = Gazetteer.from_processed(settings.data_dir, spec.extra_areas, spec.outside_areas)
    gold = yaml.safe_load(GOLD.read_text(encoding="utf-8"))
    msgs = load_messages(settings.scenario_dir, spec.start)[: args.n]
    llm = None if args.oracle else build_llm(settings, spec.id)
    provider = "oracle" if args.oracle else llm.provider.name  # type: ignore[union-attr]

    rows, results = [], []
    done: list[Processed] = []
    totals = {"decision": 0, "location": 0, "location_n": 0, "people": 0, "people_n": 0, "n": 0,
              "reviewed": 0, "revised": 0, "located": 0, "located_ok": 0, "hazard": 0, "hazard_changed": 0,
              "credibility": 0, "doubted": 0}
    for msg in msgs:
        g = gold[msg.id]
        try:
            if args.oracle:
                p = finish(msg, oracle_intake(msg.text, g), gaz, state.fire, spec.bbox)
            else:
                p = process(msg, llm, gaz, state.fire, spec.bbox, ctx=state.hazard_context(),  # type: ignore[arg-type]
                            earlier=lambda name, t: earlier_places(done, name, t))
        except BudgetExhausted as exc:
            print(f"\nStopped at {msg.id}: {exc}. Run again tomorrow; answers so far are cached.")
            break
        totals["n"] += 1
        done.append(p)
        ok_dec = p.outcome.decision.value in g["decision"]
        totals["decision"] += ok_dec
        loc_ok = None
        if g.get("location") and g["location"].get("lon") is not None:
            totals["location_n"] += 1
            if p.location.lon is not None and p.location.lat is not None:
                d = haversine_m(p.location.lon, p.location.lat, g["location"]["lon"], g["location"]["lat"])
                loc_ok = d <= g["location"]["tol_m"]
            else:
                loc_ok = False
            totals["location"] += bool(loc_ok)
        ppl_ok = None
        if g.get("people") is not None:
            totals["people_n"] += 1
            got = p.intake.output.people.count if p.intake and p.intake.output.people else None
            ppl_ok = got == g["people"]
            totals["people"] += ppl_ok
        mins = "-" if p.minutes_to_front is None else f"{p.minutes_to_front:+.0f}′"
        rows.append(f"{msg.id} {msg.time:%H:%M} {'✓' if ok_dec else '✗'} {p.outcome.decision.value:26s} "
                    f"loc:{_mark(loc_ok)} {p.location.status:11s} people:{_mark(ppl_ok)} front:{mins:>6s} "
                    f"| {p.location.label[:32]}")
        if not ok_dec:
            rows.append(f"      expected {g['decision']}; reasons: {' '.join(p.outcome.reasons)[:140]}")
        if p.intake and p.intake.rejected:
            rows.append(f"      quotes rejected by code: {p.intake.rejected}")
        lr = p.location_review
        if lr:
            totals["located"] += 1
            totals["located_ok"] += bool(lr.get("accepted"))
            tools = ", ".join(f"{x['tool']}({'/'.join(x['args'].values())})" for x in lr["steps"]) or "-"
            rows.append(f"      location agent ({lr['trigger']}): {tools} -> {lr.get('outcome') or '-'}"
                        + f": {lr.get('reason') or lr.get('error') or ''}"
                        + (f"; left from {lr['left_from']}" if lr.get("left_from") else ""))
        hr = p.hazard_review
        if hr:
            totals["hazard"] += 1
            totals["hazard_changed"] += bool(hr.get("changed"))
            rows.append(f"      hazard critic: facts {[f['id'] for f in hr['facts'] if f['severe']]}, "
                        f"{len(hr['cautions'])} cautions"
                        + (f"; changed {hr['changed']}" if hr.get("changed") else "")
                        + (f"; refused {hr['suggested']}: {hr['refused']}" if hr.get("refused") else "")
                        + (f"; ERROR {hr['error']}" if hr.get("error") else ""))
        cr = p.credibility
        if cr:
            totals["credibility"] += 1
            totals["doubted"] += cr["assessment"] != "credible"
            rows.append(f"      credibility: {[x['id'] for x in cr['signals']]} -> {cr['assessment']}"
                        + (" (capped)" if cr.get("capped") else "") + f": {cr['reason_el'][:100]}")
        rv = p.intake.review if p.intake else None
        if rv:
            totals["reviewed"] += 1
            totals["revised"] += bool(rv.get("revised"))
            rows.append(f"      critic ({', '.join(rv['triggers'])}): {len(rv['objections'])} objections"
                        + (f", {len(rv['dropped'])} dropped" if rv["dropped"] else "")
                        + (f"; revised: {'; '.join(rv['changes'])}" if rv.get("revised") else "")
                        + (f"; ERROR {rv['error']}" if rv.get("error") else ""))
        if p.error:
            rows.append(f"      ERROR: {p.error}")
        results.append({**p.to_json(), "gold": g, "decision_ok": ok_dec, "location_ok": loc_ok,
                        "people_ok": ppl_ok})

    print("\n".join(rows))
    n = max(1, totals["n"])
    print(f"\n{provider}: decision {totals['decision']}/{totals['n']} ({totals['decision'] / n:.0%}), "
          f"location {totals['location']}/{totals['location_n']}, people {totals['people']}/{totals['people_n']}, "
          f"critic reviewed {totals['reviewed']}, revised {totals['revised']}, "
          f"location agent ran {totals['located']}, accepted {totals['located_ok']}, "
          f"hazard critic ran {totals['hazard']}, changed {totals['hazard_changed']}, "
          f"credibility ran {totals['credibility']}, doubted {totals['doubted']}")
    print(callbacks(done, llm, gaz, state, spec))
    print(needs_report(done))
    print(missions(done, llm, state))
    print(field_reports(llm))
    dup = duplicates(done, gold, llm, gaz)
    print(dup["text"])
    if llm is not None:
        print(f"LLM budget used today: {llm.budget.used_today()}/{settings.llm_daily_limit}")  # type: ignore[union-attr]
    out = Path(settings.runtime_dir) / "eval" / f"dev_{provider}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    report = {"totals": totals, "duplicates": dup["rows"], "results": results}
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print(f"Details: {out}")
    return 0


def callbacks(done: list[Processed], llm, gaz: Gazetteer, state, spec) -> str:
    """The call-back loop: questions for each CALLBACK, the simulated reply, and the case read again."""
    from datetime import timedelta

    from palladion.agents.callback import plan
    from palladion.cases.simulator import Simulator
    sim = Simulator.load(state.settings.scenario_dir)
    lines = ["", "Call-backs (questions, simulated reply, new decision):"]
    for p in done:
        if p.outcome.decision.value != "CALLBACK":
            continue
        cp = plan(llm, p, p.message.channel)
        if cp is None:
            continue
        lines.append(f"  {p.message.id} [{cp.source}] " + " | ".join(q.text for q in cp.questions)
                     + (f"  (problems: {'; '.join(cp.problems)})" if cp.problems else ""))
        r = sim.reply(p.message.id)
        if r is None or llm is None:
            lines.append("      no simulated reply" if r is None else "      (oracle: not re-read)")
            continue
        m = Message(id=p.message.id, time=p.message.time + timedelta(minutes=1 + r.delay_min),
                    channel=p.message.channel, text=f"{p.message.text}\n\n[Απάντηση στην επανάκληση] {r.text}")
        q = process(m, llm, gaz, state.fire, spec.bbox, ctx=state.hazard_context())
        lines.append(f"      reply -> {q.outcome.decision.value} at {q.location.label or q.location.status}"
                     f" (was CALLBACK at {p.location.label or p.location.status})")
    return "\n".join(lines)


def earlier_places(done: list[Processed], name: str, before) -> list[dict]:  # noqa: ANN001
    """As CaseService.earlier_places, over the messages processed so far in this run."""
    from palladion.field.extract import fold
    key, out = fold(name).strip(), []
    for p in done:
        loc = p.location
        if not key or loc.lon is None or loc.lat is None or p.message.time >= before:
            continue
        if loc.status in ("resolved", "approximate") and key in fold(loc.label or ""):
            out.append({"case": p.message.id, "name": (loc.label or "").split(",")[0].strip(), "area": loc.area,
                        "status": loc.status, "lon": loc.lon, "lat": loc.lat, "extent_m": loc.uncertainty_m,
                        "minutes_before": round((before - p.message.time).total_seconds() / 60)})
    return sorted(out, key=lambda x: x["minutes_before"])


def needs_report(done: list[Processed]) -> str:
    """What the needs reader found in each message, and what code made of it (acuity, medical clock)."""
    lines = ["", "Needs (rules + agent; acuity and medical clock by code):"]
    ran = added = rejected = 0
    for p in done:
        n = p.needs or {}
        ran += n.get("check") in ("agent_checked", "agent_failed")
        added += sum(x["source"] == "agent" for x in n.get("needs", []))
        rejected += len(n.get("rejected", []))
        if n.get("needs") or n.get("sender"):
            tags = ", ".join(f"{x['tag']}«{x['quote']}»{'*' if x['source'] == 'agent' else ''}" for x in n["needs"])
            who = f" | sender {n['sender']['role']}" if n.get("sender") else ""
            clock = f" clock {n['clock_min']}′" if n.get("clock_min") else ""
            lines.append(f"  {p.message.id} {n.get('acuity') or '-':8}{clock} {tags}{who}")
    lines.append(f"needs: agent ran {ran}, added {added} (marked *), {rejected} rejected by code")
    return "\n".join(lines)


def field_reports(llm) -> str:
    """Crew reports: the rules and the agent read them, code decides what they mean for the case."""
    import yaml

    from palladion.config import REPO_ROOT
    from palladion.field.apply import outcome
    from palladion.field.extract import read
    path = REPO_ROOT / "eval" / "dev" / "mati_2018_field_gold.yaml"
    if not path.exists():
        return ""
    gold = yaml.safe_load(path.read_text(encoding="utf-8"))
    lines = ["", "Field reports (rules + agent; code decides):"]
    ok = added = rejected = 0
    for g in gold:
        r = read(llm, g["text"])
        o = outcome(r.findings, g.get("known"), r.injection, r.doubts)
        status = o.status or "none"
        right = status == g["status"] and (g.get("remaining") is None or g["remaining"] == o.remaining)
        ok += right
        mine = [f for f in r.findings if f.source == "agent"]
        added += len(mine)
        rejected += len(r.rejected)
        lines.append(f"  {g['id']} {_mark(right)} {status:9} (expected {g['status']}) [{r.check}] "
                     + ", ".join(f"{f.kind}={f.value}" for f in r.findings)
                     + (f" | agent added: {', '.join(f.kind for f in mine)}" if mine else "")
                     + (f" | rejected: {'; '.join(r.rejected)}" if r.rejected else ""))
    lines.append(f"field reports: {ok}/{len(gold)} right, agent added {added} findings, {rejected} rejected by code")
    return "\n".join(lines)


def missions(done: list[Processed], llm, state) -> str:
    """For every case that asks for a unit: the plan at the time of the message and the crew brief."""
    from datetime import timedelta

    from palladion.agents.brief import write_brief
    eng = state.mission_engine
    if eng is None:
        return "\nLand missions: no road network or no ground_units block."
    lines = ["", "Land missions (unit at the time of the message, crew brief):"]
    counts = {"agent": 0, "revised": 0, "template": 0, "veto": 0}
    for p in done:
        if p.outcome.decision.value != "DISPATCH_UNIT_CANDIDATE" or p.location.lon is None:
            continue
        o = p.intake.output if p.intake else None
        info = {"lon": p.location.lon, "lat": p.location.lat, "address": p.location.label or p.message.id,
                "people": o.people.count if o and o.people else None,
                "vulnerable": [v.kind.value for v in o.vulnerable] if o else [],
                "vulnerable_quotes": [[v.kind.value, v.quote] for v in o.vulnerable] if o else [],
                "asks_for": list(o.asks_for) if o else [],
                "situation": o.situation.quote if o and o.situation else None,
                "message": p.message.text}
        now = p.message.time
        fire_at = now + timedelta(minutes=p.minutes_to_front) if p.minutes_to_front is not None else None
        pl = eng.plan(p.message.id, p.location.lon, p.location.lat, info["vulnerable"], info["asks_for"], now, set(),
                      fire_at=fire_at)
        if pl.unit is None:
            counts["veto"] += 1
            lines.append(f"  {p.message.id} VETO: {pl.veto} alternatives {pl.alternatives}")
            continue
        b = write_brief(llm, info, pl, now)
        counts[b.source] += 1
        lines.append(f"  {p.message.id} {pl.unit.id} {pl.eta_min(now):.0f}′ via {', '.join(pl.roads) or '-'}"
                     f" [{b.source}] {b.text}" + (f"  (problems: {'; '.join(b.problems)})" if b.problems else ""))
    lines.append(f"briefs: agent {counts['agent']}, revised {counts['revised']}, template {counts['template']}, "
                 f"vetoed {counts['veto']}")
    return "\n".join(lines)


def duplicates(done: list[Processed], gold: dict, llm, gaz: Gazetteer) -> dict:
    """Messages in arrival order, each compared with the ones before it, as the live queue does.
    Without a model (oracle) only the code's candidate list is checked."""
    is_area = lambda n: gaz.find_area(n) is not None  # noqa: E731
    lines, rows = ["", "Duplicates (each message against the earlier ones):"], []
    pairs = found = in_cands = wrong = calls = 0
    for i, p in enumerate(done):
        expected = gold[p.message.id].get("duplicate_of")
        earlier = done[:i]
        cands = [c.id for c in candidates(p, earlier, is_area)]
        pairs += expected is not None
        in_cands += expected is not None and expected in cands
        got = None
        if llm is not None and cands:
            try:
                res, _ = find_duplicate(llm, p, earlier, is_area)
            except BudgetExhausted as exc:
                lines.append(f"Stopped at {p.message.id}: {exc}")
                break
            calls += 1
            got = res.match_id if res else None
            if res and res.rejected:
                lines.append(f"  {p.message.id}: the model's quotes were not found; suggestion dropped")
        if expected is None and got is None:
            continue
        ok = got == expected if llm is not None else (expected is None or expected in cands)
        found += llm is not None and expected is not None and got == expected
        wrong += got is not None and got != expected
        lines.append(f"  {p.message.id} candidates {cands or '-'} -> {got or '-':5s} expected {expected or '-':5s} "
                     f"{'✓' if ok else '✗'}")
        rows.append({"id": p.message.id, "candidates": cands, "suggested": got, "expected": expected})
    if llm is None:
        lines.append(f"oracle: gold pairs among the code's candidates {in_cands}/{pairs}")
    else:
        lines.append(f"duplicates: found {found}/{pairs} gold pairs, {wrong} wrong suggestions, "
                     f"{calls} model calls (pairs among candidates {in_cands}/{pairs})")
    return {"text": "\n".join(lines), "rows": rows}


def _mark(v: bool | None) -> str:
    return "-" if v is None else ("✓" if v else "✗")


if __name__ == "__main__":
    sys.exit(main())
