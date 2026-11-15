"""Land missions: which unit goes to a case, by which roads, and when it gets there. Code only.

Unit kind
  ambulance     someone is injured, burned, has breathing trouble, is bedridden, or they ask for medical help
  fire engine   everything else (trapped people, people who cannot leave, a fire at the house)
If no unit of that kind can get there, or the other kind gets there 15 minutes earlier, the other kind is
offered, and the reason is shown.

Route
  From the unit's base along roads, with the fire model: a road can be used before the front reaches it
  (with the safety margin) or after it has burned out; never while it burns (RoadGraph.drive_to).

Veto
  When no road reaches the place, the plan carries a veto instead of a unit: the operator sees why, and
  the alternatives: a boat if the place is by the shore, otherwise a person decides. A boat is also
  suggested when the place is by the shore and the land unit needs more than 20 minutes. Nothing is
  sent without the operator.
"""
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from palladion.domain.models import GroundUnitsSpec, UnitKind

MEDICAL = {"injury", "burns", "respiratory", "bedridden"}
SHORE_M = 400.0
SLOW_BY_LAND = timedelta(minutes=20)      # by the shore, a land unit this slow makes a boat worth a look
OTHER_KIND_GAIN = timedelta(minutes=15)   # offer the other kind when it is this much earlier
KIND_EL = {UnitKind.fire_engine: "πυροσβεστικό όχημα", UnitKind.ambulance: "ασθενοφόρο"}


@dataclass
class Unit:
    id: str
    group: str
    kind: UnitKind
    base: str
    lon: float
    lat: float
    ready_min: float
    on_scene_min: float


@dataclass
class Mission:
    id: str
    case_id: str
    unit: str
    kind: UnitKind
    depart: datetime
    arrive: datetime
    leave_scene: datetime
    back: datetime
    coords: list[tuple[float, float]]
    distance_m: float
    roads: list[str]
    warnings: list[str]
    operator: str = ""
    brief: str = ""
    brief_source: str = ""
    brief_problems: list[str] = field(default_factory=list)
    recalled_at: datetime | None = None
    override_reason: str | None = None   # the person chose another unit than the proposal, and why

    def estimated_position(self, state: str, since: datetime | None, now: datetime) -> tuple[float, float] | None:
        """An ESTIMATE along the route from the reported state and when it was reported. It never reaches
        the place before the crew says "we arrived", and never the base before they say "available"."""
        if not self.coords:
            return None
        if state in ("assigned", "unknown"):
            return self.coords[0]
        if state == "on_scene":
            return self.coords[-1]
        if state not in ("en_route", "returning", "out_of_service") or since is None:
            return None
        if state == "out_of_service":
            frac = 0.5                       # somewhere on the way: where, the crew will say
        else:
            frac = min(0.95, max(0.0, (now - since) / max(self.arrive - self.depart, timedelta(seconds=1))))
            if state == "returning":
                frac = 1.0 - frac
        return self.coords[min(len(self.coords) - 1, int(frac * (len(self.coords) - 1)))]


@dataclass
class Plan:
    case_id: str
    kind: UnitKind
    why: str
    unit: Unit | None = None
    depart: datetime | None = None
    arrive: datetime | None = None
    coords: list[tuple[float, float]] = field(default_factory=list)
    distance_m: float = 0.0
    roads: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    front_min: float | None = None       # front at the place, minutes after the unit arrives (<0: passed)
    veto: str | None = None
    alternatives: list[str] = field(default_factory=list)
    other_kind: bool = False             # the preferred kind could not go
    options: list[dict] = field(default_factory=list)   # every unit that can get there, the proposal first

    def eta_min(self, now: datetime) -> float | None:
        return None if self.arrive is None else round((self.arrive - now) / timedelta(minutes=1), 1)


def unit_kind(vulnerable: list[str], asks_for: list[str]) -> tuple[UnitKind, str]:
    med = sorted(set(vulnerable) & MEDICAL)
    if med or "medical" in asks_for:
        return UnitKind.ambulance, "ιατρική ανάγκη: " + (", ".join(med) if med else "ζητούν ιατρική βοήθεια")
    return UnitKind.fire_engine, "άνθρωποι σε κίνδυνο χωρίς αναφορά τραυματισμού"


class MissionEngine:
    def __init__(self, spec: GroundUnitsSpec, roads: Any, fire: Any, margin_min: float,
                 near_shore: Callable[[float, float], bool] | None = None):
        self.spec, self.roads, self.fire, self.margin = spec, roads, fire, margin_min
        self.near_shore = near_shore or (lambda lon, lat: False)
        self.units: list[Unit] = []
        for g in spec.groups:
            for i in range(1, g.count + 1):
                self.units.append(Unit(f"{g.prefix}-{i:02d}", g.id, g.kind, g.base.name, g.base.lon, g.base.lat,
                                       g.ready_min, g.on_scene_min))

    def unit(self, uid: str) -> Unit | None:
        return next((u for u in self.units if u.id == uid), None)

    def plan(self, case_id: str, lon: float, lat: float, vulnerable: list[str], asks_for: list[str], now: datetime,
             unavailable: set[str], only: str | None = None, fire_at: datetime | None = None) -> Plan:
        """The unit that gets there first, among those whose crews last reported them available (or that
        have no report yet). A busy unit is not planned: when it will be free is not known until it says so."""
        kind, why = unit_kind(vulnerable, asks_for)
        p = Plan(case_id, kind, why)
        # The case's own front time (the triage looks around the place) when there is one; else the model here.
        fire_at = fire_at or self.fire.arrival_time(lon, lat)
        other = UnitKind.ambulance if kind == UnitKind.fire_engine else UnitKind.fire_engine
        failed: list[str] = []

        reachable: list[tuple[Unit, Any]] = []

        def best_of(k: UnitKind):
            best = None
            for u in self.units:
                if u.kind != k or (only and u.id != only) or u.id in unavailable:
                    continue
                depart = now + timedelta(minutes=u.ready_min)
                r = self.roads.drive_to(u.lon, u.lat, lon, lat, depart, self.margin)
                if r.status != "ok":
                    failed.append(r.status)
                    continue
                reachable.append((u, r))
                if best is None or r.arrive < best[1].arrive:
                    best = (u, r)
            return best

        mine, theirs = best_of(kind), best_of(other)
        pick, note = mine, None
        if theirs is not None and (mine is None or (mine[1].arrive - theirs[1].arrive) > OTHER_KIND_GAIN):
            pick = theirs
            if mine is None:
                note = f"Κανένα {KIND_EL[kind]} δεν φτάνει· προτείνεται {KIND_EL[other]}."
            else:
                late = (mine[1].arrive - theirs[1].arrive) / timedelta(minutes=1)
                note = f"Το πρώτο {KIND_EL[kind]} φτάνει {late:.0f}′ αργότερα· προτείνεται {KIND_EL[other]}."
        if pick is not None:
            u, r = pick
            p.kind, p.unit, p.depart, p.arrive = u.kind, u, r.depart, r.arrive
            p.coords, p.distance_m = r.coords, r.distance_m
            p.roads = [n for n, _ in sorted(r.road_m.items(), key=lambda x: -x[1])[:3]]
            p.warnings = list(r.warnings)
            p.other_kind = note is not None
            if note:
                p.warnings.insert(0, note)
            if self.near_shore(lon, lat) and (r.arrive - now) > SLOW_BY_LAND:
                p.alternatives.append("sea")
                p.warnings.append(f"Η μονάδα ξηράς θέλει {(r.arrive - now) / timedelta(minutes=1):.0f}′ και η θέση "
                                  "είναι κοντά στην ακτή: εξέτασε και σκάφος.")
            if fire_at is not None:
                # One fact, shown once: the card and the brief both say it from front_min.
                p.front_min = round((fire_at - r.arrive) / timedelta(minutes=1), 1)
            rest = sorted((x for x in reachable if x[0].id != u.id), key=lambda x: x[1].arrive)
            p.options = [{"unit": x.id, "kind": x.kind.value, "base": x.base, "arrive": y.arrive.isoformat(),
                          "eta_min": round((y.arrive - now) / timedelta(minutes=1), 1), "rank": i + 1}
                         for i, (x, y) in enumerate([(u, r), *rest][:5])]
            return p
        if "off_network" in failed and len(set(failed)) == 1:
            p.veto = "Η θέση απέχει πολύ από το οδικό δίκτυο: καμία μονάδα ξηράς δεν μπορεί να φτάσει."
        elif failed:
            p.veto = "Κάθε δρόμος προς τη θέση καίγεται τώρα: καμία μονάδα ξηράς δεν φτάνει με ασφάλεια."
        else:
            p.veto = "Καμία διαθέσιμη μονάδα."
        if self.near_shore(lon, lat):
            p.alternatives.append("sea")
        p.alternatives.append("review")
        return p

    def mission(self, p: Plan, mid: str, operator: str) -> Mission:
        assert p.unit is not None and p.depart is not None and p.arrive is not None
        leave = p.arrive + timedelta(minutes=p.unit.on_scene_min)
        back = leave + (p.arrive - p.depart)          # the drive back takes as long as the drive there
        return Mission(mid, p.case_id, p.unit.id, p.kind, p.depart, p.arrive, leave, back, p.coords, p.distance_m,
                       p.roads, p.warnings, operator)
