"""Candidate duplicates for a new message, chosen by code, judged by the duplicate agent.

Blocking rules (any one is enough), only for earlier messages in the last two hours:
  * a street, landmark or coast name in common (Greek, Greeklish or English spellings meet);
  * locations within 300 m, or a precise one inside a small approximate one (radius <= 800 m);
  * the new message says it refers to an earlier report, and the earlier one came in on the same channel.
Shared town names alone ("Μάτι") are not enough: half the messages would match.
"""
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta

from palladion.agents.duplicates import DuplicateResult, run_duplicates
from palladion.agents.intake import PlaceRole
from palladion.geo.gazetteer import similarity
from palladion.geo.proj import haversine_m
from palladion.llm.client import LLM, BudgetExhausted, LLMError
from palladion.pipeline.locate import _split_in_area
from palladion.pipeline.process import Processed

WINDOW = timedelta(hours=2)
NEAR_M = 300.0
SMALL_AREA_M = 800.0
SMALL_AREA_LIMIT_M = 1000.0     # a beach or a cove; a whole settlement ("Μάτι", 1.5 km) is too coarse
MAX_CANDIDATES = 3


@dataclass
class Candidate:
    id: str
    score: float
    why: list[str]


IsArea = Callable[[str], bool]


def _names(p: Processed, is_area: IsArea) -> list[str]:
    """Specific place names (streets, beaches, landmarks) the message mentions, wherever the people are now.
    Town names are dropped: they are shared by too many messages to mean anything."""
    if not p.intake:
        return []
    out = []
    for pl in p.intake.output.places:
        if pl.role == PlaceRole.fire:
            continue
        head, _ = _split_in_area(pl.name)
        if not is_area(head):
            out.append(head)
    return out


def candidates(new: Processed, earlier: list[Processed], is_area: IsArea = lambda _: False) -> list[Candidate]:
    out = []
    n_names = _names(new, is_area)
    refers = bool(new.intake and new.intake.output.refers_to_earlier_report)
    for e in earlier:
        if e.message.id == new.message.id or not (timedelta(0) <= new.message.time - e.message.time <= WINDOW):
            continue
        why, score = [], 0.0
        shared = [a for a in n_names for b in _names(e, is_area) if similarity(a, b) >= 0.86]
        if shared:
            why.append(f"ίδιο όνομα τόπου: {shared[0]}")
            score += 2
        a, b = new.location, e.location
        if a.lon is not None and b.lon is not None and a.lat is not None and b.lat is not None:
            d = haversine_m(a.lon, a.lat, b.lon, b.lat)
            both_precise = a.status == b.status == "resolved"
            # A precise address inside a small approximate area (a named beach, a cove) also counts;
            # two approximate places never do: they share the same centre point by construction.
            approx_r = max((x.uncertainty_m or 0) for x in (a, b) if x.status == "approximate") \
                if "approximate" in (a.status, b.status) else 0
            one_inside = ({a.status, b.status} == {"resolved", "approximate"} and approx_r <= SMALL_AREA_LIMIT_M
                          and d <= min(SMALL_AREA_M, approx_r))
            if (both_precise and d <= NEAR_M) or one_inside:
                why.append(f"κοντινές θέσεις ({d:.0f} m)")
                score += 1.5
        if refers and e.message.channel == new.message.channel:
            why.append("αναφέρεται σε προηγούμενο μήνυμα, ίδιο κανάλι")
            score += 2
        if why:
            out.append(Candidate(e.message.id, score, why))
    out.sort(key=lambda c: -c.score)
    return out[:MAX_CANDIDATES]


def find_duplicate(llm: LLM, new: Processed, earlier: list[Processed],
                   is_area: IsArea = lambda _: False) -> tuple[DuplicateResult | None, list[Candidate]]:
    """None when there is nothing to ask about (no model call is made)."""
    cands = candidates(new, earlier, is_area)
    if not cands:
        return None, []
    by_id = {e.message.id: e for e in earlier}
    rows = [(c.id, f"{by_id[c.id].message.time:%H:%M}", by_id[c.id].message.channel, by_id[c.id].message.text)
            for c in cands]
    try:
        res = run_duplicates(llm, (new.message.id, new.message.channel, new.message.text), rows)
    except BudgetExhausted:
        raise                       # the caller must know the check did not run
    except LLMError:
        return None, cands
    return res, cands
