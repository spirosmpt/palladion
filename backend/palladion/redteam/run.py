"""Red team from the command line.

    python -m palladion.redteam.run --n 15             attacker agent writes 15 messages (one per category); the
                                                       desk triages them; code judges; failures become proposals
    python -m palladion.redteam.run --n 15 --scripted  the same with the scripted messages (no attacker calls)
    python -m palladion.redteam.run --proposals        list the failures waiting for a person
    python -m palladion.redteam.run --approve pr-… --by Νίκος     make it a regression test
    python -m palladion.redteam.run --reject pr-… --by Νίκος      never propose it again
    python -m palladion.redteam.run --replay           run every approved regression again

Each message is triaged on its own, at --time (default 18:10, when the front is in Mati), with all the agents of
the desk; nothing reaches the case queue, the registry or any unit. Model calls count against the daily budget.
"""
import argparse
import sys
from datetime import datetime
from typing import Any

from palladion.config import REPO_ROOT, get_settings
from palladion.redteam import harness as H
from palladion.redteam.policy import table


def case_of(p: Any) -> dict[str, Any]:
    o = p.intake.output if p.intake else None
    return {"decision": p.outcome.decision.value, "location_status": p.location.status, "label": p.location.label,
            "lon": p.location.lon, "area_inferred": "area_inferred" in p.location.flags,
            "people": o.people.count if o and o.people else None, "error": p.error,
            "flags": list(p.outcome.flags), "credibility": (p.credibility or {}).get("assessment")}


def make_runner(state: Any, llm: Any, at: datetime) -> H.Runner:
    from palladion.pipeline.process import Message, process
    spec = state.scenario.spec

    budget = getattr(llm, "budget", None)

    def run(decls: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for d in decls:
            refused = budget.refused if budget is not None else 0
            if budget is not None and refused:      # spent earlier in this batch: do not judge a half-run desk
                out[d["id"]] = {"skipped": "budget"}
                continue
            m = Message(id=d["id"], time=at, channel="sms", text=d["message"])
            p = process(m, llm, state.gazetteer, state.fire, spec.bbox, ctx=state.hazard_context())
            # An agent the desk needed was refused by the budget: the decision would be the fallback's, not the desk's.
            out[d["id"]] = {"skipped": "budget"} if budget is not None and budget.refused > refused else case_of(p)
        return out
    return run


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--n", type=int, default=0)
    ap.add_argument("--scripted", action="store_true", help="scripted messages, no attacker agent")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--time", default="18:10")
    ap.add_argument("--proposals", action="store_true")
    ap.add_argument("--approve")
    ap.add_argument("--reject")
    ap.add_argument("--by", default="")
    ap.add_argument("--replay", action="store_true")
    ap.add_argument("--pending", action="store_true",
                    help="judge declarations left unjudged when the budget ran out (no attacker calls)")
    ap.add_argument("--policy", action="store_true", help="print the policy table")
    args = ap.parse_args(argv)
    settings = get_settings()
    store = H.Store(REPO_ROOT / "eval" / "redteam", settings.runtime_dir / "redteam")

    if args.policy:
        for row in table():
            print(f"{row['id']:22} accept {', '.join(row['accept'])}\n{'':22} rules  {', '.join(row['rules'])}")
        return 0
    if args.approve or args.reject:
        row = H.decide(store, args.approve or args.reject, "approve" if args.approve else "reject", args.by)
        print(f"{row['proposal_id']}: {row['status']} by {row['decided_by']}")
        return 0
    if args.proposals:
        for p in store.read("proposed"):
            if p["status"] == "pending":
                print(f"{p['proposal_id']} [{p['category']}] {'UNSAFE ' if p['unsafe'] else ''}-> {p['decision']}\n"
                      f"    «{p['message']}»\n    " + "\n    ".join(p["reasons"]))
        return 0

    from palladion.api.state import build_state
    from palladion.llm.client import BudgetExhausted, build_llm
    state = build_state(settings)
    spec = state.scenario.spec
    llm = build_llm(settings, spec.id)
    at = datetime.fromisoformat(f"{spec.start.date().isoformat()}T{args.time}:00{spec.start.isoformat()[19:]}")
    runner = make_runner(state, llm, at)
    try:
        if args.replay:
            rows = H.replay(store, runner)
            for r in rows:
                tag = "FIXED" if r["fixed"] else "REGRESSED" if r["regressed"] else ("ok" if r["passed"] else "open")
                print(f"{r['id']} [{r['category']}] {tag} -> {r['decision']} {'; '.join(r['reasons'])}")
            print(f"regression: {sum(r['passed'] for r in rows)}/{len(rows)} pass")
            return 0
        if args.pending:
            out = H.judge_pending(store, runner)
        elif args.n <= 0:
            ap.print_help()
            return 1
        else:
            out = H.run_batch(args.n, store, None if args.scripted else llm, state.gazetteer, runner, seed=args.seed)
    except BudgetExhausted as exc:
        print(f"Stopped: {exc}. Declarations already stored stay; run again tomorrow.")
        return 2
    for r in out["results"]:
        mark = "✓" if r["passed"] else ("✗ UNSAFE" if r["unsafe"] else "✗")
        src = r["source"] + (f" ({r['fallback_reason']})" if r.get("fallback_reason") else "")
        print(f"{mark} [{r['category']}] -> {r['decision']}   [{src}]\n    «{r['message']}»")
        for why in r["reasons"]:
            print(f"      {why}")
    for k in out["skipped"]:
        print(f"… [{k['category']}] not judged ({k['why']})\n    «{k['message']}»")
    s = out["summary"]
    print(f"\nred team {out['run_id']}: {s['passed']}/{s['total']} passed, {s['unsafe']} unsafe, "
          f"{s['agent_written']} written by the attacker agent")
    if out["skipped"]:
        print(f"{len(out['skipped'])} not judged: the day's model budget ran out while the desk read them. "
              "Tomorrow:  python -m palladion.redteam.run --pending")
    if llm is not None and llm.budget is not None:
        print(f"LLM budget used today: {llm.budget.used_today()}/{settings.llm_daily_limit}")
    print("Failures wait for a person:  python -m palladion.redteam.run --proposals")
    return 0


if __name__ == "__main__":
    sys.exit(main())
