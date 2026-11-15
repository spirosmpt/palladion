"""Candidate decision for a message, from the intake fields, the location and the fire facts.

Rules, not a model: every outcome can be traced to the facts listed in `reasons`. Nothing here is
an action; each decision is a candidate that a human operator approves or changes.
"""
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from palladion.agents.intake import IntakeResult, MessageKind, Situation, Vulnerability
from palladion.pipeline.locate import Location


class Decision(StrEnum):
    EVACUATE_ROUTE_CANDIDATE = "EVACUATE_ROUTE_CANDIDATE"
    SHELTER_CANDIDATE = "SHELTER_CANDIDATE"
    SEA_RESCUE_CANDIDATE = "SEA_RESCUE_CANDIDATE"
    DISPATCH_UNIT_CANDIDATE = "DISPATCH_UNIT_CANDIDATE"
    CALLBACK = "CALLBACK"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    REPORTED_SAFE = "REPORTED_SAFE"
    MISSING_PERSON = "MISSING_PERSON"
    MONITOR = "MONITOR"
    INFO = "INFO"
    OUT_OF_AREA = "OUT_OF_AREA"


# People who cannot get themselves out: send someone rather than send directions.
NEEDS_UNIT = {Vulnerability.mobility, Vulnerability.bedridden, Vulnerability.pregnant, Vulnerability.burns,
              Vulnerability.respiratory, Vulnerability.injury, Vulnerability.no_vehicle}


VULNERABLE_EL = {
    "elderly": "ηλικιωμένοι", "child": "παιδιά", "mobility": "κινητικά προβλήματα", "bedridden": "κατάκοιτος",
    "pregnant": "έγκυος", "respiratory": "αναπνευστικό", "burns": "εγκαύματα", "injury": "τραυματισμός",
    "no_vehicle": "χωρίς όχημα", "animals": "ζώα",
}


@dataclass
class FireFacts:
    minutes_to_front: float | None     # negative: the front has already passed; None: never reaches
    reported_fire: bool                # the message itself reports flames or a burning structure nearby
    nearby_m: float | None = None      # the time is for the nearest burning part of the street or area, this far
                                       # from its point (the point itself is not reached); None: the point itself


@dataclass
class Outcome:
    decision: Decision
    priority: int                       # 0 (low) .. 100
    reasons: list[str] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)
    question_el: str | None = None


def fire_facts(minutes: float | None, intake: IntakeResult, nearby_m: float | None = None) -> FireFacts:
    reported = any(h.kind in ("flames", "fire_nearby", "structure_fire") for h in intake.output.hazards)
    return FireFacts(minutes, reported, nearby_m)


def _near(m: float | None) -> str:
    """ "at about 400 m from the point given" for a time measured at the edge of a street or an area."""
    return "" if m is None else f" σε σημείο περίπου {round(m / 50) * 50:.0f} m από τη θέση που δόθηκε"


TRAPPED = re.compile(
    r"(δεν μπορ\w* (?:να )?(?:βγ|φυγ|φυ[γκ]|κινηθ|επιστρ|γυρισ|κατεβ)\w*|κολλησ\w*|ακινητοποιημ\w*|εγκλωβ\w*|"
    r"παγιδευ\w*|(?:μας |μας εχει |εχει )?κλεισ\w* (?:η εξοδος|ο δρομος|απο (?:τη|τις) φ)|"
    r"den mpor\w* na (?:vg|fyg|fig|kin)\w*|eksodos exei kleisei|can'?t (?:get out|leave|move)|trapped|stuck)")


def trapped_quote(text: str) -> str | None:
    """Words that say the people cannot leave by themselves (exit blocked, car stuck, cannot move)."""
    t = "".join(c for c in unicodedata.normalize("NFD", text.lower()) if unicodedata.category(c) != "Mn")
    m = TRAPPED.search(t)
    return m.group(0) if m else None


def decide(intake: IntakeResult, loc: Location, fire: FireFacts, now: datetime | None = None,
           text: str = "") -> Outcome:
    o = intake.output
    vul = {v.kind for v in o.vulnerable}
    people = o.people.count if o.people and o.people.count else None

    if intake.injection_suspected:
        return Outcome(Decision.REVIEW_REQUIRED, 5, ["Το μήνυμα προσπαθεί να δώσει εντολές στο σύστημα."],
                       ["injection"])
    if o.kind == MessageKind.unverified_claim:
        return Outcome(Decision.REVIEW_REQUIRED, 10, ["Ανεπιβεβαίωτος ισχυρισμός· χρειάζεται διασταύρωση."],
                       ["rumor"])
    at_coast = o.situation is not None and o.situation.kind in (Situation.sea, Situation.beach)
    flames = any(h.kind in ("flames", "structure_fire") for h in o.hazards)
    early_flags: list[str] = []
    if o.kind == MessageKind.situational_info:
        # Safety first: flames near people who are somewhere we can place, or people at the coast
        # asking for boats, are a request for help whatever the label says.
        if loc.status in ("resolved", "approximate", "ambiguous") and (fire.reported_fire or at_coast):
            early_flags.append("upgraded_from_info")
        else:
            return Outcome(Decision.INFO, 15, ["Πληροφορία για την κατάσταση, όχι αίτημα διάσωσης."])
    if loc.status == "out_of_area":
        return Outcome(Decision.OUT_OF_AREA, 5, [f"Η τοποθεσία ({loc.label}) είναι εκτός περιοχής σεναρίου."])
    if o.kind == MessageKind.safety_report:
        return Outcome(Decision.REPORTED_SAFE, 5, ["Ο αποστολέας αναφέρει ότι είναι ασφαλής."])
    if o.kind == MessageKind.missing_person and loc.status != "resolved":
        return Outcome(Decision.MISSING_PERSON, 40, ["Αναφορά αγνοούμενου· στην επανένωση οικογενειών."])
    if o.kind == MessageKind.missing_person:
        early_flags.append("missing_at_known_address")   # someone can go to the address
    if o.kind == MessageKind.unclear and loc.status in ("none", "approximate", "ambiguous"):
        return Outcome(Decision.CALLBACK, 60, ["Το μήνυμα είναι ελλιπές."], [],
                       loc.question_el or "Πού βρίσκεστε και πόσα άτομα είστε;")

    sea = at_coast or "boats" in o.asks_for

    # Priority: time to the front dominates, then people and vulnerability.
    urgency = 50.0
    reasons: list[str] = []
    flags: list[str] = list(early_flags)
    if "upgraded_from_info" in flags:
        reasons.append("Αναφέρει φωτιά κοντά ή ανθρώπους στην ακτή: αντιμετωπίζεται ως αίτημα βοήθειας.")
    m = fire.minutes_to_front
    if m is None:
        if fire.reported_fire:
            urgency = 75
            flags.append("contradicts_model")
            reasons.append("Το μοντέλο φωτιάς δεν φτάνει εδώ, αλλά το μήνυμα αναφέρει φωτιά κοντά: "
                           "εμπιστευόμαστε την αναφορά.")
        else:
            urgency = 15
            reasons.append("Το ανασυνθεμένο μέτωπο δεν φτάνει σε αυτό το σημείο.")
    elif m <= 0:
        urgency = 95
        reasons.append(f"Κατά τη λήψη του μηνύματος το μέτωπο είχε ήδη φτάσει{_near(fire.nearby_m)} "
                       f"(πριν από {abs(m):.0f}′).")
    else:
        urgency = max(30.0, 100 - m * 1.5)
        reasons.append(f"Κατά τη λήψη του μηνύματος το μέτωπο απείχε περίπου {m:.0f}′{_near(fire.nearby_m)}.")
    if fire.nearby_m is not None:
        flags.append("front_nearby")
        reasons.append("Το ανασυνθεμένο μέτωπο δεν περνά από το ίδιο το σημείο του δρόμου· μετράει το πιο κοντινό "
                       "σημείο του που καίγεται (για ασφάλεια).")
    if people:
        urgency += min(10, people)
        reasons.append(f"{people} άτομα.")
    if vul:
        urgency += 8
        reasons.append("Ευάλωτοι: " + ", ".join(sorted(VULNERABLE_EL.get(v.value, v.value) for v in vul)) + ".")
    priority = int(max(0, min(100, urgency)))

    if o.kind == MessageKind.question_no_danger and not flames and loc.status != "none":
        return Outcome(Decision.MONITOR, min(priority, 40),
                       reasons + ["Ερώτηση χωρίς περιγραφή κινδύνου: ενημέρωση και ετοιμότητα."], flags)
    if sea and loc.status == "ambiguous":
        return Outcome(Decision.SEA_RESCUE_CANDIDATE, max(priority, 70),
                       reasons + ["Άνθρωποι στην ακτή/θάλασσα· το ακριβές σημείο θέλει επιβεβαίωση."], flags,
                       loc.question_el)
    if loc.status in ("none", "ambiguous"):
        return Outcome(Decision.CALLBACK, max(priority, 50), reasons + ["Δεν είναι γνωστή η ακριβής θέση."],
                       flags, loc.question_el)
    if sea:
        return Outcome(Decision.SEA_RESCUE_CANDIDATE, max(priority, 70), reasons + ["Άνθρωποι στην ακτή/θάλασσα."],
                       flags)
    trapped = trapped_quote(text) if text else None
    if trapped is None and any(h.kind == "blocked_road" for h in o.hazards) and (fire.reported_fire or flames):
        trapped = next(h.quote for h in o.hazards if h.kind == "blocked_road")
    stuck = trapped is not None or bool(vul & NEEDS_UNIT) or (
        o.situation is not None and o.situation.kind == Situation.sheltering)
    if loc.status == "approximate" and o.kind == MessageKind.help_request and stuck:
        # They cannot leave on their own: a unit goes towards the area while the exact address is asked.
        flags.append("area_only")
        why = f"Δεν μπορούν να φύγουν μόνοι τους («{trapped}»)." if trapped else "Δεν μπορούν να φύγουν μόνοι τους."
        return Outcome(Decision.DISPATCH_UNIT_CANDIDATE, max(priority, 70),
                       reasons + [why, "Μόνο η περιοχή είναι γνωστή: η διεύθυνση ζητείται παράλληλα."], flags,
                       loc.question_el)
    if (loc.status == "approximate" and o.kind == MessageKind.help_request and "route" in o.asks_for
            and o.reporter.value == "self"):
        # They can move and ask how to get out: directions for the area now, the exact street asked at once.
        flags.append("area_only")
        return Outcome(Decision.EVACUATE_ROUTE_CANDIDATE, priority,
                       reasons + ["Ζητούν διαδρομή και μπορούν να κινηθούν· μόνο η περιοχή είναι γνωστή, "
                                  "η διεύθυνση ζητείται παράλληλα."], flags, loc.question_el)
    if loc.status == "approximate":
        return Outcome(Decision.CALLBACK, max(priority, 50), reasons + ["Μόνο η περιοχή είναι γνωστή."], flags,
                       loc.question_el)
    if m is None and not fire.reported_fire:
        return Outcome(Decision.MONITOR, priority, reasons + ["Να παραμείνουν σε ετοιμότητα."], flags)
    if trapped is not None:
        return Outcome(Decision.DISPATCH_UNIT_CANDIDATE, max(priority, 80),
                       reasons + [f"Δεν μπορούν να φύγουν μόνοι τους («{trapped}»)."], flags + ["trapped"])
    if o.situation is not None and o.situation.kind == Situation.sheltering:
        return Outcome(Decision.DISPATCH_UNIT_CANDIDATE, max(priority, 80),
                       reasons + ["Έχουν κλειστεί σε χώρο· χρειάζεται μονάδα."], flags)
    if vul & NEEDS_UNIT:
        return Outcome(Decision.DISPATCH_UNIT_CANDIDATE, priority,
                       reasons + ["Δεν μπορούν να απομακρυνθούν μόνοι τους."], flags)
    if o.reporter.value == "third_party":
        # Directions only help someone we can talk to; the people at risk are not on the line.
        return Outcome(Decision.DISPATCH_UNIT_CANDIDATE, priority,
                       reasons + ["Αναφορά από τρίτον: οι άνθρωποι στο σημείο δεν επικοινωνούν μαζί μας."], flags)
    if o.situation is not None and o.situation.kind == Situation.vehicle and m is not None and 0 < m <= 10:
        return Outcome(Decision.SHELTER_CANDIDATE, priority,
                       reasons + ["Σε όχημα με το μέτωπο κοντά: η οδική διαφυγή μπορεί να μην προλαβαίνει."], flags)
    return Outcome(Decision.EVACUATE_ROUTE_CANDIDATE, priority, reasons, flags)
