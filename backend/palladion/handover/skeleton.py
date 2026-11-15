"""Situation report and shift handover: the skeleton. Code only.

Every line comes from a record of the desk and carries an id; the summary agent may cite only these ids
and use only the numbers in the lines it cites (agent.py). Only what has happened by the scenario time
is in it: no hindsight. Sections:
  totals       messages received, decided, closed by field reports, people reported rescued
  urgent       open cases that move people, by minutes to the front
  callbacks    call-backs still waiting for a reply
  units        land units away from base, as their crews last reported
  warnings     112 warnings sent, and proposals waiting for a person
  supervisor   supervisor alerts not yet resolved
  field        what field reports said that still needs a person: people still there, nobody found, review
"""
from datetime import datetime
from typing import Any

TOP = 8
DECIDED = {"approved", "changed", "closed", "resolved", "merged"}
ACTING = {"EVACUATE_ROUTE_CANDIDATE", "DISPATCH_UNIT_CANDIDATE", "SHELTER_CANDIDATE", "SEA_RESCUE_CANDIDATE"}
AWAY = {"assigned", "en_route", "on_scene", "returning", "out_of_service"}
DECISION_EL = {"EVACUATE_ROUTE_CANDIDATE": "εκκένωση", "DISPATCH_UNIT_CANDIDATE": "αποστολή μονάδας",
               "SHELTER_CANDIDATE": "παραμονή σε κτίριο", "SEA_RESCUE_CANDIDATE": "διάσωση από θάλασσα",
               "CALLBACK": "επανάκληση", "REVIEW_REQUIRED": "έλεγχος"}
FIELD_EL = {"resolved": "βγήκαν όλοι", "partial": "μένουν άτομα", "not_found": "δεν βρέθηκε κανείς",
            "review": "θέλει έλεγχο"}


def _item(i: str, text: str, case_ids: list[str] | None = None) -> dict[str, Any]:
    return {"id": i, "text_el": text, "case_ids": case_ids or []}


def _section(key: str, title: str, items: list[dict[str, Any]]) -> dict[str, Any]:
    return {"key": key, "title_el": title, "count": len(items), "items": items[:TOP], "truncated": len(items) > TOP}


def _hhmm(t: datetime | None) -> str:
    return t.strftime("%H:%M") if t else "—"


def _front(c: dict[str, Any]) -> str:
    m = c["minutes_left"]
    if m is None:
        return "μέτωπο: —"
    return "το μέτωπο έχει φτάσει" if m <= 0 else f"μέτωπο σε {m:.0f}′"


def _totals(s: dict[str, Any], now: datetime) -> list[dict[str, Any]]:
    cases = s["cases"]
    decided = sum(c["status"] in DECIDED for c in cases)
    by_field = sum(c["status"] == "resolved" for c in cases)
    land = sum(c.get("rescued") or 0 for c in cases)
    out = [_item("tot:messages", f"Μηνύματα ως τις {_hhmm(now)}: {len(cases)}, με απόφαση χειριστή {decided}."),
           _item("tot:field", f"Περιστατικά που έκλεισαν με αναφορά πεδίου: {by_field}· άτομα που ανέφεραν ως "
                              f"διασωθέντα οι μονάδες ξηράς: {land}.")]
    return out


def _urgent_line(c: dict[str, Any]) -> dict[str, Any]:
    people = c["people"] if c["people"] is not None else "άγνωστα"
    state = "χωρίς απόφαση" if c["status"] in ("proposed", "failed") else c["status"]
    what = DECISION_EL.get(c["decision"], c["decision"])
    text = f"{c['id']} {c['label'] or 'χωρίς θέση'}: {what}, {_front(c)}, άτομα {people}, {state}."
    return _item(f"case:{c['id']}", text, [c["id"]])


def _urgent(s: dict[str, Any]) -> list[dict[str, Any]]:
    rows = [c for c in s["cases"] if c["open"] and c["decision"] in ACTING]
    rows.sort(key=lambda c: (c["minutes_left"] if c["minutes_left"] is not None else 1e6, c["id"]))
    return [_urgent_line(c) for c in rows]


def _callbacks(s: dict[str, Any], now: datetime) -> list[dict[str, Any]]:
    out = []
    for c in s["cases"]:
        if c["callback_sent"] is None or c["callback_reply"] or c["callback_cancelled"] or not c["open"]:
            continue
        wait = (now - c["callback_sent"]).total_seconds() / 60
        out.append(_item(f"cb:{c['id']}", f"{c['id']}: ερωτήσεις στάλθηκαν {_hhmm(c['callback_sent'])}, χωρίς "
                                          f"απάντηση {wait:.0f}′.", [c["id"]]))
    return out


def _units(s: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for m in s["missions"]:
        if m["phase"] not in AWAY:
            continue
        since = f" από {_hhmm(m['since'])}" if m["since"] else ""
        out.append(_item(f"unit:{m['unit']}", f"{m['unit']} για το {m['case']}: {m['label_el']}{since}.", [m["case"]]))
    return out



def _warnings(s: dict[str, Any]) -> list[dict[str, Any]]:
    al = s.get("alerts112") or []
    out = [_item(f"w:{a['id']}", f"112 στάλθηκε {_hhmm(a['at'])} για {a['settlement']}: {a['action_el']}.")
           for a in al if a["status"] == "sent"]
    out += [_item(f"w:{a['id']}", f"112 για {a['settlement']} περιμένει απόφαση χειριστή ({a['action_el']}).")
            for a in al if a["status"] == "proposed"]
    return out


def _supervisor(s: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for a in s.get("supervisor_alerts") or []:
        esc = ", κλιμάκωση" if a["escalated"] else ""
        out.append(_item(f"sv:{a['alert_id']}", f"{a['kind_el']} ({_hhmm(a['created_at'])}{esc}): {a['fact_el']}",
                         a["case_ids"]))
    return out


def _field(s: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for c in s["cases"]:
        st = c.get("field_status")
        if not st or st == "resolved" or not c["open_or_handled"]:
            continue
        if st == "partial" and c["people"]:
            what = "μένει 1 άτομο" if c["people"] == 1 else f"μένουν {c['people']} άτομα"
        else:
            what = FIELD_EL.get(st, st)
        out.append(_item(f"field:{c['id']}", f"{c['id']}: {what} (αναφορά πεδίου).", [c["id"]]))
    return out


def build(s: dict[str, Any], now: datetime) -> dict[str, Any]:
    sections = [
        _section("totals", "Σύνολα", _totals(s, now)),
        _section("urgent", "Ανοιχτά περιστατικά που κινούν ανθρώπους (πρώτα όσα έχουν το μέτωπο κοντά)", _urgent(s)),
        _section("callbacks", "Επανακλήσεις χωρίς απάντηση", _callbacks(s, now)),
        _section("units", "Μονάδες ξηράς εκτός βάσης (όπως ανέφεραν τα πληρώματα)", _units(s)),
        _section("warnings", "Προειδοποιήσεις 112", _warnings(s)),
        _section("supervisor", "Ειδοποιήσεις του Επιβλέποντα χωρίς λύση", _supervisor(s)),
        _section("field", "Αναφορές πεδίου που θέλουν συνέχεια", _field(s)),
    ]
    return {"now": now.isoformat(), "sections": sections, "counts": {x["key"]: x["count"] for x in sections},
            "note_el": "Χρόνος σεναρίου· ό,τι έγινε μετά από αυτή την ώρα δεν μετράει."}


def facts(sk: dict[str, Any]) -> dict[str, str]:
    """id -> text for every line shown: the only things the summary may cite."""
    return {i["id"]: i["text_el"] for x in sk["sections"] for i in x["items"]}


def as_text(sk: dict[str, Any], summary: dict[str, Any] | None = None) -> str:
    """Plain text, to paste in a message or print."""
    out = [f"Αναφορά κατάστασης, {sk['now'][:16].replace('T', ' ')}", ""]
    if summary and summary.get("sentences"):
        out += [x["text"] for x in summary["sentences"]] + [""]
    for x in sk["sections"]:
        if not x["items"]:
            continue
        out.append(f"{x['title_el']} ({x['count']})")
        out += [f"  - {i['text_el']}" for i in x["items"]]
        if x["truncated"]:
            out.append(f"  … και {x['count'] - len(x['items'])} ακόμα")
        out.append("")
    return "\n".join(out).strip() + "\n"
