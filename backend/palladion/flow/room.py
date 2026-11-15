"""The operations room's agents, live: what each one wrote, whether code kept it, how long it took. Read only.

These agents do not read a citizen's message; they work for the desk:
  supervisor     looks at the whole queue and raises what is forgotten or late (supervisor.tick)
  alert_writer   writes the 112 warning text, Greek and English (the alert desk)
  crew_brief     writes the crew's card when the operator dispatches a unit (unit.dispatched)
  field_report   reads what a crew reported from the field (unit.report)
  sitrep         writes the shift handover summary (handover.created)
For every run: the replay time, what it was given, what it wrote (short), and how it ended:
  model     the agent's text, checked by code and kept
  rejected  the agent wrote, code found a problem, the template went out instead
  code      no model call (rules or template only)
  failed    the call failed; the template went out
with the seconds of the call (a cached answer: the seconds of the real call it replays) and what a person did next.
"""
from collections.abc import Callable
from datetime import datetime
from typing import Any

AGENTS = [
    ("supervisor", "Επόπτης", "βλέπει όλη την ουρά και ειδοποιεί για ό,τι ξεχάστηκε ή καθυστερεί"),
    ("alert_writer", "Συντάκτης 112", "γράφει το κείμενο της προειδοποίησης, ελληνικά και αγγλικά"),
    ("crew_brief", "Κάρτα αποστολής", "γράφει τις οδηγίες για το πλήρωμα όταν στέλνεται μονάδα"),
    ("field_report", "Αναφορά πληρώματος", "διαβάζει τι είπε το πλήρωμα από το πεδίο"),
    ("sitrep", "Παράδοση βάρδιας", "γράφει την περίληψη για την επόμενη βάρδια"),
    ("coastguard", "Σύνδεσμος Λιμενικού", "ομαδοποιεί τη θάλασσα ανά ακτή, γράφει και ιεραρχεί τα αιτήματα"),
]
STATE_EL = {"model": "κράτησε το κείμενο του πράκτορα", "rejected": "ο κώδικας το απέρριψε· μπήκε το πρότυπο",
            "code": "χωρίς μοντέλο (κανόνες)", "failed": "η κλήση απέτυχε· μπήκε το πρότυπο"}
SUPERVISOR_STATE = {"agent_checked": "model", "agent_rejected": "rejected", "agent_failed": "failed"}
FINDING_EL = {"departed": "αναχώρησαν", "arrived": "έφτασαν", "returning": "επιστρέφουν", "available": "διαθέσιμοι",
              "failed": "βλάβη", "rescued_n": "διέσωσαν", "remaining_n": "μένουν",
              "all_out": "βγήκαν όλοι", "not_found": "δεν βρέθηκε κανείς", "left_by_other_means": "έφυγαν μόνοι τους",
              "needs_more": "χρειάζονται κι άλλη μονάδα", "new_hazard": "νέος κίνδυνος"}


def _secs(calls: list[dict[str, Any] | None]) -> tuple[float | None, bool, str | None]:
    """Total seconds of the real calls, whether any was cached, and the model."""
    total, cached, model, any_ = 0.0, False, None, False
    for c in calls:
        if not c:
            continue
        s = c.get("original_seconds") if c.get("status") == "cached" else c.get("seconds")
        cached = cached or c.get("status") == "cached"
        model = c.get("model") or model
        if s is not None:
            total += float(s)
            any_ = True
    return (round(total, 2) if any_ else None), cached, model


def _short(text: str, n: int = 120) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= n else text[: n - 1] + "…"


class RoomFlow:
    def __init__(self, registry: Callable[[], Any], alerts: Callable[[], Any]):
        self._registry, self._alerts = registry, alerts

    def _run(self, out: list[dict[str, Any]], agent: str, ts: datetime, given: str, wrote: str, state: str,
             calls: list[dict[str, Any] | None], human: str | None = None, rid: str = "") -> None:
        s, cached, model = _secs(calls)
        out.append({"id": rid or f"{agent}:{len(out)}", "agent": agent, "ts": ts.isoformat(), "given": _short(given),
                    "wrote": _short(wrote, 160), "state": state, "state_el": STATE_EL[state], "seconds": s,
                    "cached": cached, "model": model, "human": human})

    def runs(self, now: datetime) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        decided: dict[str, str] = {}
        for ev in self._registry().events():
            if ev.sim_time is None or ev.sim_time > now:
                continue
            d = ev.data
            if ev.type == "supervisor.decided":
                decided[d.get("alert_id", "")] = d.get("decision", "")
        for ev in self._registry().events():
            if ev.sim_time is None or ev.sim_time > now:
                continue
            d, t = ev.data, ev.sim_time
            if ev.type == "supervisor.tick" and d.get("findings"):
                ch = d.get("check")
                state = SUPERVISOR_STATE.get(ch, "code")
                al = d.get("alerts") or []
                wrote = (f"{len(al)} ειδοποιήσεις" + (f": {al[0].get('why_el', '')}" if al else "")) if al \
                    else "καμία νέα ειδοποίηση"
                done = [decided[a["alert_id"]] for a in al if a.get("alert_id") in decided]
                human = (f"ο χειριστής αποφάσισε για {len(done)} από {len(al)}" if done else
                         ("περιμένει τον χειριστή" if al else None))
                self._run(out, "supervisor", t, f"{len(d['findings'])} ευρήματα στην ουρά", wrote, state,
                          [d.get("call")], human, f"r{ev.seq}")
            elif ev.type == "unit.dispatched" and d.get("brief"):
                src, calls = d.get("brief_source"), d.get("brief_calls") or []
                state = "model" if src in ("agent", "revised") else \
                    ("failed" if any(str(p).startswith("model:") for p in d.get("brief_problems") or []) else
                     "rejected" if calls else "code")
                self._run(out, "crew_brief", t, f"{ev.stream.split(':', 1)[1]} → {d.get('case', '—')}",
                          d["brief"], state, calls, f"αποστολή από {ev.actor.id}" +
                          (" (ξαναγράφτηκε μία φορά)" if src == "revised" else ""), f"r{ev.seq}")
            elif ev.type == "unit.report" and str(d.get("text") or "").strip():
                ch = d.get("check")
                state = {"agent_checked": "model", "agent_failed": "failed"}.get(ch, "code")
                fs = [FINDING_EL.get(f.get("kind", ""), f.get("kind", "")) + (f" {f['value']}" if not isinstance(
                    f.get("value"), bool) and f.get("value") is not None else "") for f in d.get("findings") or []]
                wrote = ", ".join(fs) or "τίποτα που να ελέγχεται"
                if d.get("rejected"):
                    wrote += f" · απορρίφθηκαν {len(d['rejected'])}"
                o = d.get("outcome") or {}
                human = "ενημέρωσε το περιστατικό" if o.get("status") else None
                self._run(out, "field_report", t, f"{d.get('unit')}: «{d.get('text')}»", wrote, state,
                          [d.get("call")], human, f"r{ev.seq}")
            elif ev.type == "coastguard.drafted":
                st = d.get("status")
                reqs = d.get("requests") or {}
                n_agent = sum(1 for r in reqs.values() if r.get("source") == "agent")
                state = {"agent_checked": "model", "agent_partial": "model", "agent_rejected": "rejected",
                         "agent_failed": "failed"}.get(str(st), "code")
                first = next((reqs[g]["text"] for g in d.get("order") or [] if g in reqs), "")
                wrote = (f"{n_agent}/{len(reqs)} αιτήματα, σειρά: {' → '.join(d.get('order') or [])} · " if reqs
                         else "") + first
                sent = next((e for e in self._registry().events("coastguard") if e.type == "coastguard.request_sent"
                             and e.sim_time is not None and e.sim_time <= now and e.sim_time >= t), None)
                self._run(out, "coastguard", t, f"{len(d.get('groups') or {})} ακτές, "
                          f"{sum(len(v) for v in (d.get('groups') or {}).values())} μηνύματα · "
                          f"{len(d.get('steps') or [])} κλήσεις εργαλείων", wrote, state, d.get("calls") or [None],
                          f"στάλθηκε από {sent.actor.id}" if sent else None, f"r{ev.seq}")
            elif ev.type == "handover.created":
                sm = d.get("summary") or {}
                call = sm.get("call")
                state = "model" if sm.get("status") == "agent" else \
                    ("failed" if any(str(p).startswith("model:") for p in sm.get("rejected") or []) else
                     "rejected" if call else "code")
                first = (sm.get("sentences") or [{}])[0]
                wrote = first.get("text") or "μόνο ο σκελετός με τους αριθμούς"
                self._run(out, "sitrep", t, "κατάσταση της αίθουσας", str(wrote), state, [call],
                          f"από {ev.actor.id}", f"r{ev.seq}")
        al = self._alerts()
        for a in (al.visible() if al else []):
            w = a.written
            if w is None or a.created > now:
                continue
            state = "model" if w.source == "agent" else \
                ("failed" if any(str(p).startswith("model:") for p in w.problems) else
                 "rejected" if w.call else "code")
            human = {"sent": "στάλθηκε", "dismissed": "απορρίφθηκε", "proposed": "περιμένει τον χειριστή",
                     "expired": "έληξε χωρίς ενέργεια", "replaced": "αντικαταστάθηκε"}.get(a.status.value)
            if a.edited:
                human = (human or "") + " (με αλλαγές)"
            self._run(out, "alert_writer", a.created, f"προειδοποίηση για {a.threat.settlement}", w.text.el, state,
                      [w.call], human, f"alert:{a.id}")
        out.sort(key=lambda r: (r["ts"], r["id"]), reverse=True)
        return out

    def view(self, now: datetime, limit: int = 200) -> dict[str, Any]:
        runs = self.runs(now)
        agents = []
        for aid, name, does in AGENTS:
            mine = [r for r in runs if r["agent"] == aid]
            timed = [r["seconds"] for r in mine if r["seconds"] is not None]
            counts = {k: sum(1 for r in mine if r["state"] == k) for k in STATE_EL}
            agents.append({"id": aid, "el": name, "does_el": does, "runs": len(mine), "counts": counts,
                           "last": mine[0] if mine else None,
                           "avg_s": round(sum(timed) / len(timed), 2) if timed else None})
        return {"now": now.isoformat(), "agents": agents, "runs": runs[:limit], "count": len(runs),
                "states_el": STATE_EL}
