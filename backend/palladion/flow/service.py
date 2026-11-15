"""Agent flow: one time-ordered stream of who handed what to whom, with how long each step took. Read only.

It is a projection of what the desk already keeps; it decides nothing:
  - for every processed message, the pipeline in order: the citizen's message -> intake -> critic -> location agent
    (with its tool steps) -> hazard critic -> credibility -> needs -> arbiter -> decision (code) -> the operator;
    then the duplicates agent and the call-back agent when they ran;
  - every human action in the registry (approve, change, reply, dispatch, crew report, 112, ...), at its time.
Each step: time (the replay clock), source and target, what passed (short), status (ok | failed | rules, when code
went on without a model), the model and the seconds the call took. A cached answer shows the seconds of the real
call it replays (original_seconds), marked as such. human_gate marks the steps that wait for a person.
"""
from collections.abc import Callable
from datetime import datetime
from typing import Any

NODE_EL = {
    "citizen": "Πολίτης", "intake": "Πρόσληψη", "critic": "Κριτής", "location": "Θέση", "tools": "Εργαλεία",
    "hazard": "Κίνδυνος", "credibility": "Αξιοπιστία", "needs": "Ανάγκες", "arbiter": "Διαιτητής",
    "decide": "Απόφαση (κώδικας)", "duplicates": "Διπλά", "callback": "Επανάκληση", "operator": "◆ Χειριστής",
    "crew": "Πλήρωμα", "brief": "Κάρτα αποστολής", "alerts": "112", "supervisor": "Επόπτης", "coastguard": "Λιμενικό",
}
ORDER = list(NODE_EL)
DECISION_EL = {
    "EVACUATE_ROUTE_CANDIDATE": "εκκένωση", "DISPATCH_UNIT_CANDIDATE": "αποστολή μονάδας",
    "SHELTER_CANDIDATE": "καταφύγιο", "SEA_RESCUE_CANDIDATE": "διάσωση από θάλασσα", "CALLBACK": "επανάκληση",
    "MISSING_PERSON": "αγνοούμενος", "REPORTED_SAFE": "ασφαλείς", "MONITOR": "παρακολούθηση", "INFO": "πληροφορία",
    "REVIEW_REQUIRED": "έλεγχος από άνθρωπο", "OUT_OF_AREA": "εκτός περιοχής",
}
KIND_EL = {
    "help_request": "ζητά βοήθεια", "safety_report": "είναι ασφαλείς", "missing_person": "αγνοούμενος",
    "situational_info": "πληροφορία", "unverified_claim": "ανεπιβεβαίωτο", "question_no_danger": "ερώτηση",
    "instruction_to_system": "εντολή προς το σύστημα", "unclear": "ασαφές",
}
HAZARD_EL = {"flames": "φλόγες", "smoke": "καπνός", "fire_nearby": "φωτιά κοντά", "structure_fire": "καίγεται κτίριο",
             "blocked_road": "κλειστός δρόμος", "traffic": "κίνηση", "other": "άλλο"}
LOC_EL = {"resolved": "σημείο", "approximate": "περίπου", "ambiguous": "αμφίσημη", "unresolved": "δεν βρέθηκε",
          "out_of_area": "εκτός περιοχής"}
LOC_OUTCOME_EL = {"found": "βρήκε", "confirmed": "επιβεβαίωσε", "refined": "βρήκε τον δρόμο",
                  "disputed": "διαφωνεί, μένει ο κώδικας", "guess": "μάντεψε, ρωτάμε τον αποστολέα",
                  "no_answer": "δεν ξεχώρισε", "rejected": "απορρίφθηκε",
                  "failed": "απέτυχε"}
HUMAN_EL = {
    "case.submitted": ("citizen", "operator", "καταχώριση μηνύματος"),
    "case.approved": ("operator", "decide", "έγκριση"),
    "case.decision_changed": ("operator", "decide", "αλλαγή απόφασης"),
    "case.closed": ("operator", "decide", "κλείσιμο"), "case.reopened": ("operator", "decide", "επαναφορά"),
    "case.merged": ("operator", "duplicates", "συγχώνευση"),
    "case.not_duplicate": ("operator", "duplicates", "όχι διπλό"),
    "callback.answered": ("citizen", "operator", "απάντηση στην επανάκληση"),
    "case.contradiction_decided": ("operator", "decide", "κρίση ασυμφωνίας"),
    "mission.approved": ("operator", "crew", "αποστολή μονάδας"), "mission.cancelled": ("operator", "crew", "ακύρωση"),
    "unit.dispatched": ("operator", "crew", "αποστολή μονάδας"),
    "case.field_outcome": ("crew", "operator", "αναφορά πληρώματος"),
    "callback.sent": ("callback", "citizen", "ερωτήσεις στον αποστολέα"),
    "alert.sent": ("operator", "alerts", "αποστολή 112"), "alert.dismissed": ("operator", "alerts", "απόρριψη 112"),
    "alert.proposed": ("alerts", "operator", "πρόταση 112"),
    "supervisor.alert": ("supervisor", "operator", "ειδοποίηση επόπτη"),
    "supervisor.decided": ("operator", "supervisor", "απόφαση σε ειδοποίηση"),
    "coastguard.request_sent": ("operator", "coastguard", "αίτημα στο Λιμενικό"),
    "case.contradiction_question": ("contra", "operator", "ερώτηση ασυμφωνίας"),
}


def _secs(call: dict[str, Any] | None) -> tuple[float | None, bool]:
    """(seconds of the real call, replayed from the cache?)"""
    if not call:
        return None, False
    if call.get("status") == "cached":
        return call.get("original_seconds"), True
    return call.get("seconds"), False


def _status(call: dict[str, Any] | None) -> str:
    if not call:
        return "rules"
    st = call.get("status")
    return "ok" if st in ("ok", "cached", "retry_ok") else "failed"


class FlowBuilder:
    def __init__(self, cases: Callable[[], Any], registry: Callable[[], Any]):
        self._cases, self._registry = cases, registry

    def _edge(self, out: list[dict[str, Any]], case: str | None, ts: datetime, src: str, dst: str, kind: str,
              payload: str, call: dict[str, Any] | None = None, status: str | None = None,
              human_gate: bool = False) -> None:
        s, cached = _secs(call)
        out.append({"id": f"{case or '-'}:{len(out)}", "case": case, "ts": ts.isoformat(), "src": src, "dst": dst,
                    "kind": kind, "payload": payload[:160], "status": status or _status(call),
                    "seconds": None if s is None else round(float(s), 2), "cached": cached,
                    "model": (call or {}).get("model") or None, "human_gate": human_gate})

    def pipeline(self, c: Any) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        p = c.processed
        if p is None:
            return out
        j = p.to_json()
        cid, ts = c.message.id, c.message.time
        self._edge(out, cid, ts, "citizen", "intake", "message", c.message.text, status="ok")
        it = j.get("intake")
        if not it:
            self._edge(out, cid, ts, "intake", "decide", "call", f"απέτυχε: {j.get('error') or 'χωρίς απάντηση'}",
                       status="failed")
        else:
            o = it.get("output") or {}
            hz = ", ".join(HAZARD_EL.get(h.get("kind", ""), h.get("kind", "")) for h in o.get("hazards") or [])
            n = (o.get("people") or {}).get("count")
            self._edge(out, cid, ts, "intake", "critic" if it.get("review") else "location", "call",
                       f"{KIND_EL.get(o.get('kind', ''), o.get('kind', ''))} · "
                       f"άτομα {n if n is not None else '—'}" + (f" · {hz}" if hz else "")
                       + (" · εντολή προς το σύστημα" if it.get("injection_suspected") else ""), it.get("call"))
            rv = it.get("review")
            if rv:
                calls = rv.get("calls") or [None]
                self._edge(out, cid, ts, "critic", "location", "call",
                           f"ενστάσεις {len(rv.get('objections') or [])}"
                           + (" · διόρθωσε την ανάγνωση" if rv.get("revised") else ""), calls[-1])
        lr = j.get("location_review")
        loc = j.get("location") or {}
        if lr:
            for st in lr.get("steps") or []:
                self._edge(out, cid, ts, "location", "tools", "tool_call",
                           f"{st.get('tool')}({', '.join(str(v) for v in (st.get('args') or {}).values())}) → "
                           f"{st.get('results')} αποτελέσματα", status="failed" if st.get("error") else "ok")
            calls = lr.get("calls") or [None]
            said = LOC_OUTCOME_EL.get(lr.get("outcome") or "", "δεκτή" if lr.get("accepted") else "κράτησε τον κώδικα")
            self._edge(out, cid, ts, "location", "hazard", "call",
                       f"{loc.get('label') or '—'} ({LOC_EL.get(str(loc.get('status')), loc.get('status'))})"
                       + f" · {said}"
                       + (f": {lr['proposed']}" if lr.get("proposed") else ""), calls[-1])
        else:
            self._edge(out, cid, ts, "location", "hazard", "data", f"από κώδικα: {loc.get('label') or '—'} "
                       f"({LOC_EL.get(str(loc.get('status')), loc.get('status'))})", status="rules")
        hr = j.get("hazard_review")
        if hr:
            self._edge(out, cid, ts, "hazard", "credibility", "call",
                       f"προειδοποιήσεις {len(hr.get('cautions') or [])}"
                       + (f" · άλλαξε: {hr['changed']}" if hr.get("changed") else ""), hr.get("call"))
        cr = j.get("credibility")
        if cr:
            self._edge(out, cid, ts, "credibility", "needs", "call", f"{cr.get('assessment')}", cr.get("call"))
        nd = j.get("needs") or {}
        if nd.get("call"):
            tags = ", ".join(n.get("tag", "") for n in nd.get("needs") or [])
            self._edge(out, cid, ts, "needs", "arbiter", "call",
                       (tags or "καμία ανάγκη") + (" · ασθενοφόρο" if nd.get("ambulance") else ""), nd.get("call"))
        arb = j.get("arbiter") or {}
        if arb.get("rule") not in (None, "none"):
            self._edge(out, cid, ts, "arbiter", "decide", "decision", f"κανόνας {arb['rule']}"
                       + (" · άλλαξε την απόφαση" if arb.get("changed") else ""), status="rules")
        oc = j.get("outcome") or {}
        dec = str(oc.get("decision") or "")
        m = j.get("minutes_to_front")
        self._edge(out, cid, ts, "decide", "operator", "decision",
                   f"πρόταση: {DECISION_EL.get(dec, dec)}" + (f" · μέτωπο {m:+.0f}′" if m is not None else ""),
                   status="rules", human_gate=True)
        dup = c.duplicate
        if dup is not None:
            self._edge(out, cid, ts, "duplicates", "operator", "call",
                       f"ίδιο με {dup.match_id}" if dup.match_id else "κανένα διπλό", dup.call)
        cb = c.callback
        if cb is not None:
            self._edge(out, cid, ts, "callback", "operator", "call", " | ".join(q.text for q in cb.questions)[:160]
                       if getattr(cb, "questions", None) else "ερωτήσεις", cb.call,
                       status=None if cb.source == "agent" else "rules", human_gate=True)
        return out

    def human(self, now: datetime) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for ev in self._registry().events():
            if ev.sim_time is None or ev.sim_time > now or ev.type not in HUMAN_EL:
                continue
            src, dst, label = HUMAN_EL[ev.type]
            case = ev.stream.split(":", 1)[1] if ev.stream.startswith("case:") else ev.data.get("case_id") \
                or ev.data.get("case")
            what = label
            if ev.type == "case.decision_changed" and ev.data.get("decision"):
                what += f" → {DECISION_EL.get(ev.data['decision'], ev.data['decision'])}"
            elif ev.type == "case.approved" and ev.data.get("decision"):
                what += f": {DECISION_EL.get(ev.data['decision'], ev.data['decision'])}"
            elif ev.data.get("unit"):
                what += f": {ev.data['unit']}"
            kind = "human_action" if ev.actor.kind.value == "human" else "handoff"
            call = ev.data.get("call") if isinstance(ev.data.get("call"), dict) else None
            if ev.type == "case.contradiction_question":
                kept = ev.data.get("source") == "agent"
                what = f"{'κράτησε' if kept else 'σταθερή'}: «{ev.data.get('text', '')}»"
                self._edge(out, case, ev.sim_time, src, dst, "call", what, call,
                           status=None if kept or call is None else "rejected")
            else:
                self._edge(out, case, ev.sim_time, src, dst, kind, what, status="ok", human_gate=src == "operator")
            out[-1]["id"] = f"r{ev.seq}"
        return out

    def view(self, now: datetime, case_id: str | None = None, limit: int = 300) -> dict[str, Any]:
        cases = [c for c in self._cases().visible(now) if case_id is None or c.message.id == case_id]
        events = [e for c in cases for e in self.pipeline(c)]
        events += [e for e in self.human(now) if case_id is None or e["case"] == case_id]
        events.sort(key=lambda e: (e["ts"], e["case"] or "", e["id"].startswith("r"), int(e["id"].split(":")[-1])
                                   if ":" in e["id"] else 0))
        nodes: dict[str, dict[str, Any]] = {n: {"id": n, "el": NODE_EL[n], "events": 0, "calls": 0, "failed": 0,
                                                "rules": 0, "seconds": [], "last": None} for n in ORDER}
        for e in events:
            n = nodes.get(e["src"])
            if n is None:
                continue
            n["events"] += 1
            n["last"] = e["ts"]
            if e["kind"] in ("call", "tool_call"):
                n["calls"] += 1
            if e["status"] == "failed":
                n["failed"] += 1
            if e["status"] == "rules":
                n["rules"] += 1
            if e["seconds"] is not None and e["kind"] == "call":
                n["seconds"].append(e["seconds"])
        for n in nodes.values():
            s = n.pop("seconds")
            n["avg_s"] = round(sum(s) / len(s), 2) if s else None
            n["max_s"] = round(max(s), 2) if s else None
        total = [e["seconds"] for e in events if e["seconds"] is not None and e["kind"] == "call"]
        return {"now": now.isoformat(), "case": case_id, "events": events[-limit:], "count": len(events),
                "nodes": list(nodes.values()), "model_seconds": round(sum(total), 1) if total else None}
