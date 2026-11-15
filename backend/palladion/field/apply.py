"""What a field report means for its case. Code only; nothing here moves a unit or a person.

  all out (counts add up, "όλοι", or they had left on their own)   -> resolved: the case closes as rescued
  people remain                                                   -> back in the queue for a unit, with the
                                                                     number left; a person approves any new unit
  nobody at the address                                           -> call-back: are they still there?
  a new hazard, or counts that do not add up                      -> human review, with the reason
  "we need another unit"                                          -> stays as an open item for the operator
A report with only a status ("we left", "we arrived") changes nothing in the case.
"""
from dataclasses import dataclass, field
from typing import Any

from palladion.field.extract import Finding


@dataclass
class FieldOutcome:
    status: str | None                  # resolved | partial | not_found | review | None (status only)
    decision: str | None = None         # the new proposed decision when the case goes back to the queue
    reason_el: str = ""
    rescued: int | None = None
    remaining: int | None = None
    conflicts: list[str] = field(default_factory=list)
    open_items: list[str] = field(default_factory=list)
    quotes: list[str] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return {"status": self.status, "decision": self.decision, "reason_el": self.reason_el,
                "rescued": self.rescued, "remaining": self.remaining, "conflicts": self.conflicts,
                "open_items": self.open_items, "quotes": self.quotes}


def outcome(findings: list[Finding], known: int | None, injection: bool = False,
            doubts: list[str] | None = None) -> FieldOutcome:
    values: dict[str, Any] = {}
    conflicts: list[str] = []
    for f in findings:
        if f.kind in values and values[f.kind] != f.value:
            conflicts.append(f"Διαφορετικοί αριθμοί για το ίδιο πράγμα στην ίδια αναφορά ({f.kind}).")
        values[f.kind] = f.value
    out = FieldOutcome(status=None, quotes=[f.quote for f in findings if f.kind in
                                              ("rescued_n", "remaining_n", "all_out", "not_found",
                                               "left_by_other_means", "needs_more", "new_hazard")])
    if not out.quotes and not doubts:
        return out                                            # a status report: nothing for the case
    rescued = values.get("rescued_n")
    remaining = values.get("remaining_n")
    if values.get("all_out") and remaining is None:
        remaining = 0
    if known is not None:
        if any(n is not None and n > known for n in (rescued, remaining)):
            conflicts.append(f"Η αναφορά λέει περισσότερα άτομα από όσα ξέραμε ({known}).")
        if rescued is not None and remaining is not None and rescued + remaining != known:
            conflicts.append(f"Σώθηκαν {rescued} και μένουν {remaining}, αλλά ξέραμε για {known}.")
        if rescued is not None and remaining is None:
            remaining = known - rescued if rescued <= known else None
    elif rescued is not None and remaining is None:
        conflicts.append("Δεν ξέραμε πόσοι ήταν: η αναφορά δεν λέει αν μένει κανείς.")
    if values.get("not_found") and (rescued or remaining):
        conflicts.append("«Δεν βρήκαμε κανέναν» μαζί με αριθμούς ατόμων στην ίδια αναφορά.")
    if values.get("left_by_other_means") and (rescued or remaining or values.get("not_found")):
        conflicts.append("«Έφυγαν μόνοι τους» μαζί με άλλο αποτέλεσμα στην ίδια αναφορά.")
    if injection:
        conflicts.append("Η αναφορά περιέχει κάτι σαν εντολή προς το σύστημα.")
    if doubts:
        conflicts.append("Το πλήρωμα δεν είναι σίγουρο ότι δεν έμεινε κανείς: " + "; ".join(f"«{d}»" for d in doubts))
        out.quotes += doubts
    out.rescued, out.remaining, out.conflicts = rescued, remaining, conflicts
    if values.get("needs_more"):
        out.open_items.append("Το πλήρωμα ζητά κι άλλη μονάδα: αποφασίζει ο χειριστής.")
    if conflicts:
        out.status, out.decision = "review", "REVIEW_REQUIRED"
        out.reason_el = "Η αναφορά πεδίου δεν συμφωνεί με όσα ξέραμε· επιβεβαίωση με το πλήρωμα."
    elif values.get("not_found"):
        out.status, out.decision = "not_found", "CALLBACK"
        out.reason_el = "Το πλήρωμα δεν βρήκε κανέναν στη διεύθυνση· επανάκληση: είναι ακόμα εκεί;"
    elif values.get("left_by_other_means"):
        out.status, out.remaining = "resolved", 0
        out.reason_el = "Είχαν φύγει μόνοι τους, σύμφωνα με το πλήρωμα."
    elif remaining == 0:
        out.status = "resolved"
        out.reason_el = "Το πλήρωμα αναφέρει ότι βγήκαν όλοι."
    elif remaining is not None:
        out.status, out.decision = "partial", "DISPATCH_UNIT_CANDIDATE"
        who = "Μένει 1 άτομο" if remaining == 1 else f"Μένουν {remaining} άτομα"
        out.reason_el = f"{who}· ξανά στην ουρά για μονάδα (εγκρίνει άνθρωπος)."
    if values.get("new_hazard"):
        out.open_items.append("Νέος κίνδυνος στο σημείο: να ξαναδεί ο χειριστής τα μέσα και την πρόσβαση.")
        if out.status != "resolved":   # people still there: no new unit before a person looks
            out.status, out.decision = "review", "REVIEW_REQUIRED"
            hazard = "Νέος κίνδυνος στο σημείο, κατά το πλήρωμα."
            out.reason_el = f"{out.reason_el} {hazard}".strip()
    if out.status is None and out.open_items:
        out.status, out.decision = "review", "REVIEW_REQUIRED"
        out.reason_el = out.open_items[0]
    return out
