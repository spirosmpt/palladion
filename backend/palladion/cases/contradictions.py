"""Contradictions between sources: one claim in a message against one other source the desk already has.

Each detector compares ONE claim with ONE other source and returns an item with both sides quoted and a strength:
  medium  another dated source disagrees: an item to look at, and a question for the sender
  strong  a newer or more specific source disagrees in a way that changes who is in danger: look before acting
Nothing here changes a decision, sends anyone or closes a case. A person decides which side holds (or that it is not a
contradiction); the decision is a registry event (`case.contradiction_decided`, human only) and stays with the item.
Unknown is never a contradiction: no estimate, no count, no position -> no item.

Sources compared:
  fire model     the reconstructed front at the case's location, against what the sender says they see
  linked message another message merged into this case (counts, place, "safe" against "help")
  crew report    what a crew reported at the place, against a message that came after it
"""
import re
from collections.abc import Callable
from datetime import datetime
from typing import Any

from palladion.geo.proj import haversine_m
from palladion.textnorm import normalise

ACTING = {"EVACUATE_ROUTE_CANDIDATE", "DISPATCH_UNIT_CANDIDATE", "SHELTER_CANDIDATE", "SEA_RESCUE_CANDIDATE"}
SEES_FIRE = {"flames", "fire_nearby", "structure_fire"}
FAR = re.compile(r"(?:πολυ |αρκετα )?μακρια|απο μακρια|δεν (?:βλεπ\w*|φαιν\w*)(?: \w+){0,2} φωτια|far away|no fire")
FAR_MODEL_MIN = 30.0     # the sender sees flames, the model puts the front more than this away (or never)
NEAR_MODEL_MIN = 10.0    # the sender says it is far, the model puts the front this close or already there
APART_M = 300.0


def _item(kind: str, strength: str, a: dict[str, Any], b: dict[str, Any], text_el: str,
          question_el: str | None) -> dict[str, Any]:
    return {"id": f"{kind}:{a['ref']}:{b['ref']}", "kind": kind, "strength": strength, "a": a, "b": b,
            "text_el": text_el, "question_el": question_el, "decision": None}


def _people(c: Any) -> tuple[int | None, str]:
    o = c.processed.intake.output if c.processed and c.processed.intake else None
    if o is None or o.people is None or o.people.count is None:
        return None, ""
    return o.people.count, o.people.quote or ""


def _decision(c: Any) -> str | None:
    return c.final_decision or (c.processed.outcome.decision.value if c.processed else None)


def _mins(m: float) -> str:
    return f"{round(abs(m))}′"


def fire_items(c: Any) -> list[dict[str, Any]]:
    p = c.processed
    if p is None or p.intake is None:
        return []
    cid, m = c.message.id, p.minutes_to_front
    out = []
    seen = [h for h in p.intake.output.hazards if h.kind in SEES_FIRE and h.quote]
    if seen and (m is None or m > FAR_MODEL_MIN):
        model = "δεν φτάνει σε αυτό το σημείο" if m is None else f"φτάνει εκεί {_mins(m)} μετά το μήνυμα"
        out.append(_item(
            "flames_vs_model", "medium",
            {"source": "message", "ref": cid, "quote": seen[0].quote},
            {"source": "fire_model", "ref": "front", "quote": f"Το ανασυνθεμένο μέτωπο {model}."},
            "Ο αποστολέας λέει ότι βλέπει φωτιά κοντά, ενώ το μοντέλο φωτιάς δεν τη δίνει εκεί τόσο νωρίς. "
            "Η απόφαση ήδη εμπιστεύεται το μήνυμα· το μοντέλο είναι ανασύνθεση.",
            "Βλέπετε φλόγες τώρα; Πόσο μακριά από εσάς, και προς ποια κατεύθυνση;"))
    nt = normalise(c.message.text)
    far = FAR.search(nt.text)
    if far and m is not None and m <= NEAR_MODEL_MIN:
        s, e = nt.span(far.start(), far.end())
        quote = c.message.text[s:e]
        near = getattr(p, "front_nearby_m", None)
        when = f"έχει ήδη περάσει ({_mins(m)} πριν από το μήνυμα)" if m <= 0 else f"φτάνει σε {_mins(m)}"
        if near is None:
            out.append(_item(
                "far_vs_model", "strong",
                {"source": "message", "ref": cid, "quote": quote},
                {"source": "fire_model", "ref": "front", "quote": f"Το ανασυνθεμένο μέτωπο {when} από εκεί."},
                "Ο αποστολέας λέει ότι η φωτιά είναι μακριά, ενώ το μοντέλο τη δίνει εκεί ή πολύ κοντά. "
                "Αν το μοντέλο έχει δίκιο, ο κίνδυνος υποτιμάται.",
                "Κοιτάξτε γύρω σας τώρα: βλέπετε καπνό ή φλόγες; Από ποια μεριά;"))
        else:
            # The model does not reach the point itself, only the nearest part of a long street or a wide area:
            # both can be right. Worth a look and a question (where exactly?), not a strong disagreement.
            dist = f"{round(near / 50) * 50:.0f} m"
            out.append(_item(
                "far_vs_model", "medium",
                {"source": "message", "ref": cid, "quote": quote},
                {"source": "fire_model", "ref": "front",
                 "quote": f"Το ανασυνθεμένο μέτωπο {when} σε σημείο περίπου {dist} από τη θέση που δόθηκε· "
                          "την ίδια τη θέση δεν τη φτάνει."},
                "Ο αποστολέας λέει ότι η φωτιά είναι μακριά· το μοντέλο φέρνει το μέτωπο μόνο στην άκρη του δρόμου ή "
                "της περιοχής. Μπορεί να ισχύουν και τα δύο: η θέση του αποστολέα κρίνει.",
                "Πού ακριβώς είστε στον δρόμο; Βλέπετε καπνό ή καμένα από κάποια μεριά;"))
    return out


def linked_items(c: Any, get: Callable[[str], Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    cid = c.message.id
    n0, q0 = _people(c)
    d0 = _decision(c)
    loc0 = c.processed.location if c.processed else None
    for lid in c.linked:
        other = get(lid)
        if other is None or other.processed is None:
            continue
        n1, q1 = _people(other)
        if n0 is not None and n1 is not None and n0 != n1:
            out.append(_item(
                "count_linked", "medium",
                {"source": "message", "ref": cid, "quote": q0 or str(n0)},
                {"source": "linked_message", "ref": lid, "quote": q1 or str(n1)},
                f"Τα μηνύματα για τους ίδιους ανθρώπους δίνουν διαφορετικό αριθμό: {n0} και {n1}. "
                f"Για τον σχεδιασμό μετράει ο μεγαλύτερος ({max(n0, n1)}) μέχρι να επιβεβαιωθεί.",
                "Πόσοι ακριβώς είστε μαζί τώρα;"))
        loc1 = other.processed.location
        if (loc0 is not None and loc0.status == "resolved" and loc1.status == "resolved" and loc0.lon is not None
                and loc1.lon is not None and loc0.lat is not None and loc1.lat is not None
                and haversine_m(loc0.lon, loc0.lat, loc1.lon, loc1.lat) > APART_M):
            d = haversine_m(loc0.lon, loc0.lat, loc1.lon, loc1.lat)
            out.append(_item(
                "place_linked", "medium",
                {"source": "message", "ref": cid, "quote": loc0.label},
                {"source": "linked_message", "ref": lid, "quote": loc1.label},
                "Τα μηνύματα για τους ίδιους ανθρώπους δείχνουν δύο σημεία που απέχουν περίπου "
                f"{round(d / 10) * 10} m.",
                "Πού ακριβώς βρίσκεστε τώρα; Μετακινηθήκατε;"))
        d1 = _decision(other)
        if {d0, d1} & ACTING and "REPORTED_SAFE" in {d0, d1}:
            safe, help_ = (cid, lid) if d0 == "REPORTED_SAFE" else (lid, cid)
            later = max([c, other], key=lambda x: x.message.time).message.id
            out.append(_item(
                "safe_vs_help", "strong",
                {"source": "message" if help_ == cid else "linked_message", "ref": help_,
                 "quote": (c if help_ == cid else other).message.text[:120]},
                {"source": "message" if safe == cid else "linked_message", "ref": safe,
                 "quote": (c if safe == cid else other).message.text[:120]},
                f"Ένα μήνυμα ζητά βοήθεια και ένα λέει ότι είναι ασφαλείς. Νεότερο είναι το {later}.",
                "Είστε όλοι ασφαλείς τώρα, ή μένει κάποιος πίσω;"))
    return out


def crew_items(c: Any, get: Callable[[str], Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    cid = c.message.id
    msgs = [c] + [x for x in (get(i) for i in c.linked) if x is not None]
    for f in c.field_reports:
        o, at = f["outcome"], f.get("at")
        crew = {"source": "crew", "ref": f["report"], "quote": f["text"]}
        if o["status"] in ("resolved", "not_found") and at:
            t = datetime.fromisoformat(at)
            for x in msgs:
                if x.message.time > t and _decision(x) in ACTING:
                    out.append(_item(
                        "crew_vs_newer_message", "strong", crew,
                        {"source": "message" if x is c else "linked_message", "ref": x.message.id,
                         "quote": x.message.text[:120]},
                        f"Το πλήρωμα ανέφερε «{'βγήκαν όλοι' if o['status'] == 'resolved' else 'δεν βρέθηκε κανείς'}», "
                        f"αλλά νεότερο μήνυμα ({x.message.id}) ζητά βοήθεια στο ίδιο περιστατικό.",
                        "Ποιος είναι ακόμα εκεί, και πού ακριβώς;"))
        n, q = _people(c)
        if o["status"] == "resolved" and o.get("rescued") is not None and n is not None and o["rescued"] < n:
            out.append(_item(
                "crew_count", "medium", crew, {"source": "message", "ref": cid, "quote": q or str(n)},
                f"Το πλήρωμα ανέφερε {o['rescued']} διασωθέντες και ότι βγήκαν όλοι· το μήνυμα έλεγε {n}.",
                "Είναι όλοι μαζί σας; Έφυγε κάποιος με άλλο τρόπο;"))
    return out


def detect(c: Any, get: Callable[[str], Any], decided: dict[str, dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """All items for a case, each with the person's decision on it if there is one. Recomputed on every read."""
    if c.processed is None:
        return []
    items = fire_items(c) + linked_items(c, get) + crew_items(c, get)
    seen, out = set(), []
    for it in items:
        if it["id"] in seen:
            continue
        seen.add(it["id"])
        it["decision"] = (decided or {}).get(it["id"])
        out.append(it)
    return out
