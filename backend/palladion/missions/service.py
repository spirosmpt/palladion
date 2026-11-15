"""Land missions desk: for a case that needs a unit, the plan (code), and what the operator decided.

The plan is recomputed on every read from the scenario time: the unit that gets there first along roads
the fire allows, among units whose crews last reported them available (or have not reported yet), or a
veto with the alternatives. Sending a unit and recalling it are human events in the registry
(`unit.dispatched`, `mission.cancelled`). The crew brief is written when the unit is sent, by the brief
agent with its checks, or from the template; the operator sees which.
Where a unit is and what it is doing comes only from its crew's reports (field/service.py). The times of
the plan are estimates and are shown as such.
"""
import threading
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

from palladion.agents.brief import write_brief
from palladion.engines.missions import Mission, MissionEngine, Plan
from palladion.engines.registry.registry import Actor, ActorKind, Registry
from palladion.field.service import FieldService

SENDS_UNIT = "DISPATCH_UNIT_CANDIDATE"


class MissionError(Exception):
    pass


# What the operator confirms before a land unit leaves. Recorded with the dispatch; the API requires all of them.
CHECKLIST = {
    "place": "η θέση είναι σωστή (διεύθυνση και ακρίβεια στον χάρτη)",
    "route": "είδα τη διαδρομή και πότε φτάνει εκεί το μέτωπο",
    "warnings": "διάβασα τις προειδοποιήσεις της διαδρομής",
    "crew": "μίλησα με το πλήρωμα και είναι έτοιμο",
}


class MissionService:
    def __init__(self, engine: MissionEngine, registry: Registry, llm: Any,
                 case_info: Callable[[str], dict[str, Any] | None], field: FieldService):
        """`case_info(case_id)` -> {lon, lat, address, people, vulnerable, asks_for, situation, message,
        decision, open} or None when the case is unknown or has no position."""
        self.engine, self.registry, self.llm, self.field = engine, registry, llm, field
        self._case_info = case_info
        self.missions: list[Mission] = []
        self._lock = threading.RLock()
        self._restore()

    def unavailable(self, now: datetime) -> set[str]:
        return {u.id for u in self.engine.units if self.field.busy(u.id, now)}

    def _plan(self, case_id: str, now: datetime, only: str | None = None) -> tuple[dict[str, Any], Plan]:
        info = self._case_info(case_id)
        if info is None:
            raise MissionError("Η υπόθεση δεν έχει θέση στον χάρτη: καμία μονάδα δεν μπορεί να σχεδιαστεί.")
        busy = self.unavailable(now) - ({only} if only else set())
        p = self.engine.plan(case_id, info["lon"], info["lat"], info.get("vulnerable", []), info.get("asks_for", []),
                             now, busy, only=only, fire_at=info.get("fire_at"))
        return info, p

    def plan(self, case_id: str, now: datetime) -> dict[str, Any]:
        with self._lock:
            info, p = self._plan(case_id, now)
            return {"case": case_id, "decision": info.get("decision"), "open": info.get("open", True),
                    "can_send": info.get("decision") == SENDS_UNIT and info.get("open", True) and p.unit is not None,
                    "plan": plan_json(p, now),
                    "missions": [self.mission_json(m, now) for m in self.missions if m.case_id == case_id]}

    def dispatch(self, case_id: str, operator: str, now: datetime, unit: str | None = None,
                 reason: str | None = None, checklist: list[str] | None = None) -> Mission:
        """Send the proposed unit, or another one with a reason: a person who overrides the plan says why.
        `checklist`: the checks the operator ticked; when given, every item of CHECKLIST must be there."""
        reason = (reason or "").strip() or None
        if checklist is not None:
            missing = [k for k in CHECKLIST if k not in checklist]
            if missing:
                raise MissionError("Πριν από την αποστολή: " + "; ".join(CHECKLIST[k] for k in missing))
        with self._lock:
            if unit and self.field.busy(unit, now):
                raise MissionError(f"Η μονάδα {unit} δεν έχει αναφέρει ότι είναι διαθέσιμη.")
            _, best = self._plan(case_id, now)
            rank = 1
            if unit and best.unit is not None and unit != best.unit.id:
                rank = next((o["rank"] for o in best.options if o["unit"] == unit), 0)
                if not reason or len(reason) < 5:
                    raise MissionError(f"Η πρόταση είναι η {best.unit.id}· για άλλη μονάδα γράψε γιατί.")
            info, p = self._plan(case_id, now, only=unit)
            if info.get("decision") != SENDS_UNIT:
                raise MissionError("Η απόφαση της υπόθεσης δεν είναι αποστολή μονάδας.")
            if not info.get("open", True):
                raise MissionError("Η υπόθεση είναι κλειστή ή συγχωνευμένη.")
            if p.unit is None:
                raise MissionError(p.veto or "Καμία μονάδα.")
        brief = write_brief(self.llm, info, p, now)            # outside the lock: may call the model
        with self._lock:
            n = sum(1 for m in self.missions if m.case_id == case_id) + 1
            m = self.engine.mission(p, f"{case_id}-{n}", operator or "operator")
            m.brief, m.brief_source, m.brief_problems = brief.text, brief.source, brief.problems
            m.override_reason = reason if rank != 1 else None
            stream = f"unit:{m.unit}"
            self.registry.append(stream, "unit.dispatched", Actor(kind=ActorKind.human, id=m.operator),
                                 {"case": case_id, "mission": m.id, "ordered": now.isoformat(),
                                  "depart": m.depart.isoformat(), "arrive": m.arrive.isoformat(),
                                  "brief": m.brief, "brief_source": m.brief_source, "brief_calls": brief.calls,
                                  "brief_problems": brief.problems, "rank": rank,
                                  "override_reason": reason if rank != 1 else None,
                                  "checklist": sorted(checklist) if checklist is not None else None},
                                 expected_version=self.registry.version(stream), sim_time=now)
            self.missions.append(m)
            self.field.assign(m.unit, m.id, now, case=case_id)
            return m

    def recall(self, mission_id: str, operator: str, now: datetime) -> Mission:
        with self._lock:
            m = next((x for x in self.missions if x.id == mission_id), None)
            if m is None:
                raise MissionError("unknown mission")
            if self.field.mission_phase(m.id, now) in ("done", "recalled"):
                raise MissionError("Η αποστολή έχει ήδη τελειώσει.")
            stream = f"unit:{m.unit}"
            self.registry.append(stream, "mission.cancelled", Actor(kind=ActorKind.human, id=operator or "operator"),
                                 {"mission": m.id, "case": m.case_id},
                                 expected_version=self.registry.version(stream), sim_time=now)
            m.recalled_at = now
            self.field.cancel(m.id, now)
            return m

    def _restore(self) -> None:
        for ev in self.registry.events():
            if ev.actor.kind is not ActorKind.human or not ev.stream.startswith("unit:"):
                continue
            if ev.type == "unit.dispatched" and "case" in ev.data:
                ordered = datetime.fromisoformat(ev.data["ordered"])
                info = self._case_info(ev.data["case"])
                if info is None:
                    continue
                busy = {m.unit for m in self.missions if m.recalled_at is None} - {ev.stream[5:]}
                p = self.engine.plan(ev.data["case"], info["lon"], info["lat"], info.get("vulnerable", []),
                                     info.get("asks_for", []), ordered, busy, only=ev.stream[5:],
                                     fire_at=info.get("fire_at"))
                if p.unit is None:
                    continue
                m = self.engine.mission(p, ev.data["mission"], ev.actor.id)
                m.brief, m.brief_source = ev.data.get("brief", ""), ev.data.get("brief_source", "")
                m.override_reason = ev.data.get("override_reason")
                self.missions.append(m)
                self.field.assign(m.unit, m.id, ordered, case=m.case_id)
            elif ev.type == "mission.cancelled" and ev.sim_time is not None:
                gone = next((x for x in self.missions if x.id == ev.data.get("mission")), None)
                if gone is not None:
                    gone.recalled_at = ev.sim_time
                    self.field.cancel(gone.id, ev.sim_time)

    def mission_json(self, m: Mission, now: datetime) -> dict[str, Any]:
        st = self.field.state(m.unit, now)
        phase = self.field.mission_phase(m.id, now)
        since = datetime.fromisoformat(st["since"]) if st["since"] and st["mission"] == m.id else None
        pos = m.estimated_position(phase, since, now) if st["mission"] == m.id else None
        return {**mission_json(m, now), "phase": phase, "position": list(pos) if pos else None,
                "reports": [r.to_json() for r in self.field.reports_for(mission=m.id, now=now)]}

    def snapshot(self, now: datetime) -> dict[str, Any]:
        with self._lock:
            units = []
            for u in self.engine.units:
                st = self.field.state(u.id, now)
                m = next((x for x in self.missions if x.id == st["mission"]), None)
                since = datetime.fromisoformat(st["since"]) if st["since"] else None
                pos = m.estimated_position(st["state"], since, now) if m else None
                units.append({"id": u.id, "kind": u.kind.value, "base": u.base, "state": st["state"],
                              "label_el": st["label_el"], "since": st["since"], "mission": st["mission"],
                              "case": m.case_id if m else None, "position": list(pos) if pos else [u.lon, u.lat],
                              "estimated": m is not None and st["state"] in ("en_route", "returning"),
                              "last_report": st["last_report"]})
            note = self.engine.spec.note
            return {"now": now.isoformat(), "units": units,
                    "missions": [self.mission_json(m, now) for m in self.missions],
                    "note": note.model_dump() if note else None}


def _iso(t: datetime | None) -> str | None:
    return t.isoformat() if t else None


def plan_json(p: Plan, now: datetime) -> dict[str, Any]:
    return {"kind": p.kind.value, "why": p.why, "unit": p.unit.id if p.unit else None,
            "base": p.unit.base if p.unit else None, "depart": _iso(p.depart), "arrive": _iso(p.arrive),
            "eta_min": p.eta_min(now), "distance_km": round(p.distance_m / 1000, 1), "roads": p.roads,
            "warnings": p.warnings, "front_min": p.front_min, "veto": p.veto, "alternatives": p.alternatives,
            "other_kind": p.other_kind, "options": p.options, "route": [list(c) for c in p.coords]}


def mission_json(m: Mission, now: datetime) -> dict[str, Any]:
    """The plan of a mission. Every time here is an estimate; the state comes from reports."""
    return {"id": m.id, "case": m.case_id, "unit": m.unit, "kind": m.kind.value,
            "depart": m.depart.isoformat(), "arrive": m.arrive.isoformat(), "leave_scene": m.leave_scene.isoformat(),
            "back": m.back.isoformat(), "eta_min": round((m.arrive - now) / timedelta(minutes=1), 1),
            "roads": m.roads, "warnings": m.warnings, "brief": m.brief, "brief_source": m.brief_source,
            "brief_problems": m.brief_problems, "operator": m.operator, "recalled_at": _iso(m.recalled_at),
            "override_reason": m.override_reason,
            "route": [list(c) for c in m.coords]}
