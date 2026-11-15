"""Land units, as their crews report them. Estimates never move a unit.

A unit is assigned when a person sends it (missions). After that its state changes
only with a report: from its crew (or the crew simulator in a demo, marked as such) or from an
operator's button. The estimated times of the plan are shown as estimates, and the map draws an
estimated position, never a reported one.

  assigned -> en_route ("φύγαμε") -> on_scene ("φτάσαμε", "πήραμε 3") -> returning ("επιστρέφουμε")
  -> available ("διαθέσιμοι")
  a breakdown ("βλάβη") holds the unit out of service until someone reports it available again.
A report about a mission that is not the unit's current one is kept but not applied.

What a report says about the people (rescued, still there, nobody found) is applied to the case by
code (field/apply.py, CaseService.apply_field). Every report is an event in the registry.
"""
import hashlib
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from palladion.engines.registry.registry import Actor, ActorKind, Registry
from palladion.field.apply import FieldOutcome, outcome
from palladion.field.extract import FieldReading, Finding, read

LABEL_EL = {"unknown": "χωρίς αναφορά", "available": "διαθέσιμη", "assigned": "ανατέθηκε", "en_route": "καθ' οδόν",
            "on_scene": "επί τόπου", "returning": "επιστρέφει",
            "out_of_service": "εκτός υπηρεσίας"}
MOVES = {"departed": "en_route", "arrived": "on_scene", "rescued_n": "on_scene", "returning": "returning",
         "available": "available", "failed": "out_of_service"}
BUTTONS = {"en_route", "on_scene", "returning", "available", "out_of_service"}
BUSY = {"assigned", "en_route", "on_scene", "returning", "out_of_service"}


@dataclass
class Assignment:
    unit: str
    mission: str
    at: datetime
    case: str | None = None
    cancelled_at: datetime | None = None


@dataclass
class Report:
    id: str
    unit: str
    mission: str | None
    at: datetime
    text: str
    source: str                       # crew | operator
    actor: str
    simulated: bool
    findings: list[Finding] = field(default_factory=list)
    check: str = "rules"
    rejected: list[str] = field(default_factory=list)
    button: str | None = None
    outcome: FieldOutcome | None = None
    applied: bool = True
    note: str | None = None
    call: dict | None = None          # the field-report agent's call, if it ran

    def to_json(self) -> dict[str, Any]:
        return {"id": self.id, "unit": self.unit, "mission": self.mission, "at": self.at.isoformat(),
                "text": self.text, "source": self.source, "actor": self.actor, "simulated": self.simulated,
                "findings": [f.model_dump() for f in self.findings], "check": self.check, "rejected": self.rejected,
                "button": self.button, "outcome": self.outcome.to_json() if self.outcome else None,
                "applied": self.applied, "note": self.note, "call": self.call}


class FieldError(Exception):
    pass


class FieldService:
    def __init__(self, registry: Registry, llm: Any,
                 case_people: Callable[[str], int | None] = lambda cid: None,
                 on_outcome: Callable[[str, Report], None] = lambda cid, r: None):
        self.registry, self.llm = registry, llm
        self._case_people, self._on_outcome = case_people, on_outcome
        self.assignments: list[Assignment] = []
        self.reports: list[Report] = []
        self._sim_keys: set[str] = set()
        self._lock = threading.RLock()
        self._restore()

    # -- assignments (from the missions desk) -------------------------------------------------------
    def assign(self, unit: str, mission: str, at: datetime, case: str | None = None) -> None:
        with self._lock:
            if not any(a.mission == mission for a in self.assignments):
                self.assignments.append(Assignment(unit, mission, at, case))

    def cancel(self, mission: str, at: datetime) -> None:
        with self._lock:
            for a in self.assignments:
                if a.mission == mission and a.cancelled_at is None:
                    a.cancelled_at = at

    def assignment(self, mission: str) -> Assignment | None:
        return next((a for a in self.assignments if a.mission == mission), None)

    # -- the state of a unit, from reports ------------------------------------------------------------
    def _timeline(self, unit: str, now: datetime) -> dict[str, Any]:
        rows: list[tuple[datetime, int, str, Any]] = []
        for a in self.assignments:
            if a.unit == unit and a.at <= now:
                rows.append((a.at, 0, "assign", a))
                if a.cancelled_at is not None and a.cancelled_at <= now:
                    rows.append((a.cancelled_at, 1, "cancel", a))
        for r in self.reports:
            if r.unit == unit and r.at <= now:
                rows.append((r.at, 2, "report", r))
        st: dict[str, Any] = {"state": "unknown", "since": None, "mission": None, "last": None}
        for at, _, kind, x in sorted(rows, key=lambda r: (r[0], r[1])):
            if kind == "assign":
                if st["state"] != "out_of_service":
                    st.update(state="assigned", since=at)
                st["mission"] = x.mission
            elif kind == "cancel":
                if st["mission"] == x.mission:
                    st["mission"] = None
                    if st["state"] != "out_of_service":
                        st.update(state="unknown", since=at)
            else:
                st["last"] = x
                if not x.applied:
                    continue
                new = _state_from(x)
                if new is None:
                    continue
                if st["state"] == "out_of_service" and new != "available":
                    continue                     # only "available" clears a breakdown
                if new != st["state"]:
                    st.update(state=new, since=at)
                if new == "available":
                    st["mission"] = None
        return st

    def state(self, unit: str, now: datetime) -> dict[str, Any]:
        with self._lock:
            st = self._timeline(unit, now)
        last = st["last"]
        return {"state": st["state"], "label_el": LABEL_EL[st["state"]],
                "since": st["since"].isoformat() if st["since"] else None, "mission": st["mission"],
                "last_report": last.to_json() if last else None}

    def busy(self, unit: str, now: datetime) -> bool:
        with self._lock:
            return self._timeline(unit, now)["state"] in BUSY

    def mission_phase(self, mission: str, now: datetime) -> str:
        """The unit's reported state while this is its mission; afterwards done or recalled."""
        a = self.assignment(mission)
        if a is None or a.at > now:
            return "planned"
        if a.cancelled_at is not None and a.cancelled_at <= now:
            return "recalled"
        with self._lock:
            st = self._timeline(a.unit, now)
        if st["mission"] == mission:
            return st["state"]
        return "out_of_service" if st["state"] == "out_of_service" else "done"

    def reports_for(self, mission: str | None = None, unit: str | None = None,
                    now: datetime | None = None) -> list[Report]:
        with self._lock:
            return [r for r in self.reports if (mission is None or r.mission == mission)
                    and (unit is None or r.unit == unit) and (now is None or r.at <= now)]

    # -- reports -------------------------------------------------------------------------------------
    def report(self, unit: str, text: str, at: datetime, source: str = "crew", actor: str = "operator",
               simulated: bool = False, button: str | None = None, mission: str | None = None,
               use_agent: bool = True, sim_key: str | None = None) -> Report:
        from palladion.textclean import clean
        text = clean(text)
        if button is not None and button not in BUTTONS:
            raise FieldError(f"unknown status {button}")
        if not text.strip() and button is None:
            raise FieldError("Γράψε την αναφορά ή διάλεξε κατάσταση.")
        if button is not None and source != "operator":
            raise FieldError("Μόνο ο χειριστής αλλάζει κατάσταση με κουμπί.")
        with self._lock:
            if sim_key is not None and sim_key in self._sim_keys:
                raise FieldError("already reported")
            active = self._timeline(unit, at)["mission"]
        reading = read(self.llm, text, use_agent) if text.strip() else FieldReading(findings=[], check="button")
        mission = mission or active
        applied = mission is None or mission == active
        a = self.assignment(mission) if mission else None
        out: FieldOutcome | None = None
        if a is not None and a.case and applied:
            out = outcome(reading.findings, self._case_people(a.case), reading.injection, reading.doubts)
        rid = "fr-" + hashlib.sha256(f"{unit}|{at.isoformat()}|{text}|{button}|{sim_key}".encode()).hexdigest()[:12]
        r = Report(rid, unit, mission, at, text, source, actor, simulated, reading.findings, reading.check,
                   reading.rejected, button, out, applied,
                   None if applied else f"Η μονάδα έχει άλλη τρέχουσα αποστολή ({active}).", reading.call)
        who = (Actor(kind=ActorKind.system, id="crew-simulator") if simulated
               else Actor(kind=ActorKind.human, id=actor or "operator"))
        stream = f"unit:{unit}"
        with self._lock:
            self.registry.append(stream, "unit.report", who, {**r.to_json(), "sim_key": sim_key},
                                 expected_version=self.registry.version(stream), sim_time=at)
            self.reports.append(r)
            if sim_key:
                self._sim_keys.add(sim_key)
        if out is not None and out.status is not None and a is not None and a.case:
            self._on_outcome(a.case, r)
        return r

    def simulated(self, key: str) -> bool:
        with self._lock:
            return key in self._sim_keys

    def _restore(self) -> None:
        for ev in self.registry.events():
            if ev.type != "unit.report" or ev.sim_time is None:
                continue
            d = ev.data
            o = d.get("outcome")
            r = Report(d["id"], d["unit"], d.get("mission"), ev.sim_time, d.get("text", ""), d.get("source", "crew"),
                       d.get("actor", ev.actor.id), bool(d.get("simulated")),
                       [Finding(**f) for f in d.get("findings", [])], d.get("check", "rules"), d.get("rejected", []),
                       d.get("button"), FieldOutcome(**o) if o else None, d.get("applied", True), d.get("note"),
                       d.get("call"))
            self.reports.append(r)
            if d.get("sim_key"):
                self._sim_keys.add(d["sim_key"])


def _state_from(r: Report) -> str | None:
    if r.button:
        return r.button
    moves = [MOVES[f.kind] for f in sorted(r.findings, key=lambda f: r.text.find(f.quote)) if f.kind in MOVES]
    if "out_of_service" in moves:
        return "out_of_service"
    if not moves and r.outcome is not None and r.outcome.status is not None:
        return "on_scene"                # an outcome without a status word is said from the scene
    return moves[-1] if moves else None
