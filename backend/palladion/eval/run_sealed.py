"""Run the sealed set once and print only a summary.

    python -m palladion.eval.run_sealed

Reads eval/sealed/mati_2018_sealed.yaml (written by a person, never seen by the code's author).
Prints totals only, so the set stays sealed. Per-message results go to runtime/eval/sealed_<date>.json
for later, when the set is opened and moved to development.
"""
import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import yaml

from palladion.api.state import build_state
from palladion.config import REPO_ROOT, get_settings
from palladion.geo.gazetteer import Gazetteer
from palladion.geo.proj import haversine_m
from palladion.llm.client import BudgetExhausted, build_llm
from palladion.pipeline.process import Message, process

SEALED = REPO_ROOT / "eval" / "sealed" / "mati_2018_sealed.yaml"
VALID = {"EVACUATE_ROUTE_CANDIDATE", "DISPATCH_UNIT_CANDIDATE", "SHELTER_CANDIDATE", "SEA_RESCUE_CANDIDATE",
         "CALLBACK", "MISSING_PERSON", "REPORTED_SAFE", "MONITOR", "INFO", "REVIEW_REQUIRED", "OUT_OF_AREA"}
LOCATION_TOL_M = 900.0
UNKNOWN = {"άγνωστο", "αγνωστο", "unknown", "", None}


def expected_point(where: str | None, gaz: Gazetteer) -> tuple[float, float, float] | None:
    """Resolve the person's "Οδός, Περιοχή" to a point with the same gazetteer; None if it cannot."""
    if where in UNKNOWN:
        return None
    parts = [p.strip() for p in str(where).split(",") if p.strip()]
    area = gaz.find_area(parts[-1]) if parts else None
    if len(parts) >= 2:
        cands = gaz.find(parts[0], kind="street")
        if area is not None:
            cands = [c for c in cands if c.entry.area == area.name] or cands
            if cands:
                c = min(cands, key=lambda c: haversine_m(c.entry.lon, c.entry.lat, area.lon, area.lat))
                return c.entry.lon, c.entry.lat, max(LOCATION_TOL_M, c.entry.extent_m)
        elif len(cands) == 1:
            return cands[0].entry.lon, cands[0].entry.lat, max(LOCATION_TOL_M, cands[0].entry.extent_m)
    if area is not None:
        return area.lon, area.lat, 1500.0
    return None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--file", type=Path, default=SEALED)
    args = ap.parse_args(argv)
    if not args.file.exists():
        print(f"Δεν βρέθηκε το {args.file}. Αντίγραψε το mati_2018_sealed.template.yaml ως mati_2018_sealed.yaml "
              "και γράψε τα μηνύματά σου.")
        return 1

    raw = yaml.safe_load(args.file.read_text(encoding="utf-8"))
    items = [m for m in raw.get("messages", []) if not m.get("example")]
    problems = []
    for m in items:
        for k in ("id", "time", "channel", "text", "expected"):
            if not m.get(k):
                problems.append(f"{m.get('id', '?')}: λείπει το '{k}'")
        bad = [d for d in (m.get("expected") or {}).get("decision", []) if d not in VALID]
        if bad:
            problems.append(f"{m.get('id')}: άγνωστη απόφαση {bad}")
    if not items:
        print("Δεν υπάρχουν μηνύματα (όσα έχουν example: true δεν μετράνε).")
        return 1
    if problems:
        print("Διόρθωσε πρώτα:\n  " + "\n  ".join(problems))
        return 1

    settings = get_settings()
    state = build_state(settings)
    spec = state.scenario.spec
    gaz = Gazetteer.from_processed(settings.data_dir, spec.extra_areas, spec.outside_areas)
    llm = build_llm(settings, spec.id)

    n = dec_ok = loc_ok = loc_n = ppl_ok = ppl_n = rejected = failed = 0
    unsafe = 0          # expected someone to go / move, system said they are fine or just informational
    results = []
    for m in items:
        h, mi = (int(x) for x in str(m["time"]).split(":"))
        msg = Message(id=m["id"], time=spec.start.replace(hour=h, minute=mi), channel=m["channel"], text=m["text"])
        try:
            p = process(msg, llm, gaz, state.fire, spec.bbox, ctx=state.hazard_context())
        except BudgetExhausted as exc:
            print(f"Σταμάτησε: {exc}")
            break
        exp = m["expected"]
        n += 1
        got = p.outcome.decision.value
        ok = got in exp["decision"]
        dec_ok += ok
        acting = {"EVACUATE_ROUTE_CANDIDATE", "DISPATCH_UNIT_CANDIDATE", "SHELTER_CANDIDATE", "SEA_RESCUE_CANDIDATE"}
        if set(exp["decision"]) & acting and got in {"REPORTED_SAFE", "INFO", "MONITOR", "OUT_OF_AREA"}:
            unsafe += 1
        pt = expected_point(exp.get("where"), gaz)
        lk = None
        if pt is not None:
            loc_n += 1
            lk = p.location.lon is not None and p.location.lat is not None and \
                haversine_m(p.location.lon, p.location.lat, pt[0], pt[1]) <= pt[2]
            loc_ok += bool(lk)
        pk = None
        if exp.get("people") is not None:
            ppl_n += 1
            got_p = p.intake.output.people.count if p.intake and p.intake.output.people else None
            pk = got_p == exp["people"]
            ppl_ok += pk
        rejected += len(p.intake.rejected) if p.intake else 0
        failed += p.error is not None
        results.append({**p.to_json(), "expected": exp, "decision_ok": ok, "location_ok": lk, "people_ok": pk})

    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M")
    out = Path(settings.runtime_dir) / "eval" / f"sealed_{stamp}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"results": results}, ensure_ascii=False, indent=1, default=str), encoding="utf-8")

    print("ΣΥΝΟΨΗ σφραγισμένου σετ (στείλε μόνο αυτό)")
    print(f"  μηνύματα:                 {n}")
    print(f"  σωστή απόφαση:            {dec_ok}/{n}")
    print(f"  επικίνδυνα λάθη:          {unsafe}  (έπρεπε να κινηθεί κάποιος, το σύστημα είπε 'ασφαλείς/πληροφορία')")
    print(f"  σωστή θέση:               {loc_ok}/{loc_n}  (όσα είχαν θέση που βρίσκεται στον χάρτη)")
    print(f"  σωστός αριθμός ατόμων:    {ppl_ok}/{ppl_n}")
    print(f"  παραθέματα που απέρριψε ο κώδικας: {rejected}")
    print(f"  αποτυχίες πράκτορα:       {failed}")
    print(f"  μοντέλο: {llm.provider.model_for('default')}")
    print(f"(Λεπτομέρειες, για όταν ανοίξει το σετ: {out})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
