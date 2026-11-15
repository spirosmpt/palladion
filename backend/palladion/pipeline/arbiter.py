"""Arbiter: when the agents pull in different directions, a fixed table says which one holds. Code only.

The agents run one after the other (intake, critic, location, hazard critic, credibility, needs), and each may add a
flag or, for the hazard critic, a safer decision. The table below is read top to bottom and the first rule that matches
decides; the case keeps a record of which rule it was, what each agent said, and whether the decision changed.

  1 injection         an instruction to the system         -> human review (already decided by the intake rules)
  2 likely_false      credibility says probably false, the proposal moves people or sends someone
                                                             -> human review: verify before anyone moves
  3 doubtful + safer  credibility doubts it, the hazard critic chose a safer decision -> the safer decision holds
  4 doubtful          credibility doubts it, the proposal moves people or sends someone
                                                             -> the proposal holds (life safety first), with a check
  5 area inferred     the area was inferred among same-name streets -> the proposal holds, confirm the area
  6 none              no disagreement
No rule makes a decision less safe, and no rule sends anyone: every proposal still waits for a person.
"""
from typing import Any

from palladion.pipeline.decide import Decision, Outcome

ACTING = {Decision.EVACUATE_ROUTE_CANDIDATE, Decision.DISPATCH_UNIT_CANDIDATE, Decision.SHELTER_CANDIDATE,
          Decision.SEA_RESCUE_CANDIDATE}


def arbitrate(outcome: Outcome, location_flags: list[str], credibility: dict[str, Any] | None,
              hazard: dict[str, Any] | None) -> dict[str, Any]:
    """Apply the table to `outcome` (in place when rule 2 changes it) and return the record."""
    cred = (credibility or {}).get("assessment") or "credible"
    safer = bool(hazard and hazard.get("changed"))
    said = {"credibility": cred, "hazard_changed": (hazard or {}).get("changed"),
            "location": [f for f in location_flags if f in ("area_inferred", "either_or", "operator_point")]}
    before = outcome.decision.value
    rule, why = "none", "Οι πράκτορες δεν διαφωνούν."
    if "injection" in outcome.flags:
        rule, why = "injection", "Εντολή προς το σύστημα μέσα στο μήνυμα: έλεγχος από άνθρωπο."
    elif cred == "likely_false" and outcome.decision in ACTING:
        rule, why = "likely_false", ("Ο έλεγχος αξιοπιστίας το κρίνει πιθανώς ψευδές: "
                                     "επαλήθευση πριν μετακινηθεί κανείς.")
        outcome.decision = Decision.REVIEW_REQUIRED
        outcome.flags.append("arbiter_changed")
        outcome.reasons.insert(0, f"Διαιτητής: {why}")
    elif cred == "doubtful" and safer:
        rule, why = "doubtful_safer", ("Αμφίβολο μήνυμα, αλλά ο κριτής κινδύνου πρότεινε ασφαλέστερη απόφαση: "
                                       "ισχύει αυτή.")
    elif cred == "doubtful" and outcome.decision in ACTING:
        rule, why = "doubtful", ("Αμφίβολο μήνυμα: η πρόταση μένει (πρώτα η ζωή), αλλά επιβεβαίωσε με τον αποστολέα "
                                 "πριν από την αποστολή.")
    elif "area_inferred" in location_flags and outcome.decision in ACTING:
        rule, why = "area_inferred", ("Η περιοχή συμπεράνθηκε ανάμεσα σε ομώνυμους δρόμους: η πρόταση μένει, "
                                      "επιβεβαίωσε την περιοχή.")
    return {"rule": rule, "why_el": why, "said": said, "before": before, "after": outcome.decision.value,
            "changed": before != outcome.decision.value}
