"""One message through the whole chain: intake agent -> location -> fire facts -> candidate decision."""
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel

from palladion.agents.intake import IntakeResult, run_intake
from palladion.engines.fire.model import FireEngine
from palladion.geo.gazetteer import Gazetteer
from palladion.llm.client import LLM, LLMError
from palladion.pipeline.decide import Decision, Outcome, decide, fire_facts
from palladion.pipeline.locate import Location, locate


class Message(BaseModel):
    id: str
    time: datetime
    channel: str
    text: str
    point: tuple[float, float] | None = None     # (lon, lat) an operator marked on the map for this message
    entered_by: str | None = None                # the operator who typed it in (a call, the radio), if any


def operator_location(point: tuple[float, float], gaz: Gazetteer) -> Location:
    """The operator marked where the people are: that point is the location, named after the nearest street."""
    from palladion.geo.proj import haversine_m
    lon, lat = point
    streets = [e for e in gaz.entries if e.kind == "street" and e.name]
    near = min(streets, key=lambda e: haversine_m(lon, lat, e.lon, e.lat), default=None)
    label = "Σημείο χειριστή"
    area = None
    if near is not None and haversine_m(lon, lat, near.lon, near.lat) <= max(250.0, near.extent_m):
        label, area = f"κοντά στην {near.name}, {near.area} (σημείο χειριστή)", near.area
    return Location("resolved", lon, lat, 50.0, label, area, flags=["operator_point"])


def load_messages(scenario_dir: Path, start: datetime) -> list[Message]:
    """Messages give scenario times as HH:MM on the scenario's day, in its UTC offset."""
    raw = yaml.safe_load((scenario_dir / "messages.yaml").read_text(encoding="utf-8"))
    out = []
    for m in raw["messages"]:
        h, mi = (int(x) for x in str(m["time"]).split(":"))
        out.append(Message(id=m["id"], time=start.replace(hour=h, minute=mi, second=0, microsecond=0),
                           channel=m["channel"], text=m["text"]))
    return sorted(out, key=lambda m: m.time)


@dataclass
class Processed:
    message: Message
    intake: IntakeResult | None
    location: Location
    minutes_to_front: float | None
    outcome: Outcome
    error: str | None = None
    location_review: dict | None = None        # what the location agent searched and whether code accepted it
    hazard_review: dict | None = None          # facts, cautions and any safer decision from the hazard critic
    credibility: dict | None = None            # signals of doubt and the reviewer's assessment
    needs: dict | None = None                  # medical and physical needs with quotes, acuity, who is writing
    pii: list[str] | None = None               # kinds of personal data masked before the agents read the message
    arbiter: dict | None = None                # which rule settled any disagreement between the agents
    front_nearby_m: float | None = None        # the front time is for a place this far from the point, not the point

    def to_json(self) -> dict[str, Any]:
        return {
            "message": self.message.model_dump(mode="json"),
            "intake": self.intake.model_dump(mode="json") if self.intake else None,
            "location": asdict(self.location),
            "minutes_to_front": None if self.minutes_to_front is None else round(self.minutes_to_front, 1),
            "front_nearby_m": None if self.front_nearby_m is None else round(self.front_nearby_m),
            "outcome": {**asdict(self.outcome), "decision": self.outcome.decision.value},
            "error": self.error,
            "location_review": self.location_review,
            "hazard_review": self.hazard_review,
            "credibility": self.credibility,
            "needs": self.needs,
            "pii": self.pii,
            "arbiter": self.arbiter,
        }


def process(msg: Message, llm: LLM, gaz: Gazetteer, fire: FireEngine,
            bbox: tuple[float, float, float, float], agents: bool = True, ctx: Any = None,
            earlier: Callable[[str, datetime], list[dict[str, Any]]] | None = None) -> Processed:
    """Every agent reads the masked text (phones, e-mails, ID numbers hidden); the case keeps the original."""
    from palladion.pipeline.redact import redact
    masked, pii = redact(msg.text)
    msg = msg.model_copy(update={"text": masked})
    p = _process(msg, llm, gaz, fire, bbox, agents, ctx, earlier)
    if pii:
        p.outcome.flags.append("pii_masked")
        p.pii = pii
    return p


def _process(msg: Message, llm: LLM, gaz: Gazetteer, fire: FireEngine,
             bbox: tuple[float, float, float, float], agents: bool, ctx: Any,
             earlier: Callable[[str, datetime], list[dict[str, Any]]] | None = None) -> Processed:
    try:
        intake = run_intake(llm, msg.text, msg.channel)
        if agents:
            from palladion.agents.critic import review
            intake, rv = review(llm, intake, msg.text, msg.channel)
            intake.review = rv.model_dump(mode="json") if rv else None
    except LLMError as exc:
        # Visible failure: the case still reaches the queue, for a person to read. A point the operator marked
        # still places it, with its time to the front; the rules still read the needs.
        from palladion.agents.needs import read as read_needs
        loc = operator_location(msg.point, gaz) if msg.point else Location("none")
        return Processed(msg, None, loc, _minutes(loc, fire, msg.time),
                         Outcome(Decision.REVIEW_REQUIRED, 50, [f"Ο πράκτορας πρόσληψης απέτυχε: {exc}"],
                                 ["agent_failed"]), str(exc), needs=read_needs(None, msg.text))
    loc = operator_location(msg.point, gaz) if msg.point else locate(intake.output, gaz, bbox, msg.text)
    loc_review = None
    if agents and not msg.point:          # a point the operator marked is not searched again
        from palladion.agents.location import LocationTools, needs_agent, run_location
        mine = [p for p in intake.output.places if p.role.value in ("person", "other")]
        trigger = needs_agent(loc, any(p.kind.value == "street" for p in mine),
                              any(p.kind.value == "landmark" for p in mine), has_place=bool(mine))
        if trigger:
            named = [a for a in (gaz.find_area(p.name) for p in intake.output.places) if a is not None]
            tools = LocationTools(gaz, fire, msg.time, bbox, named, roads=getattr(ctx, "roads", None),
                                  coast=getattr(ctx, "coast", None),
                                  earlier=(lambda name: earlier(name, msg.time)) if earlier else None)
            loc, lrv = run_location(llm, msg.text, tools, loc, trigger)
            loc_review = lrv.model_dump(mode="json")
    p = finish(msg, intake, gaz, fire, bbox, loc)
    p.location_review = loc_review
    if agents:
        from palladion.agents.hazard import facts, needs_review, run_hazard
        if p.outcome.decision.value in ("EVACUATE_ROUTE_CANDIDATE", "DISPATCH_UNIT_CANDIDATE",
                                        "SHELTER_CANDIDATE", "SEA_RESCUE_CANDIDATE"):
            fs = facts(intake, p.location, p.minutes_to_front, p.outcome.decision, msg.time, ctx, msg.text)
            if needs_review(p.outcome.decision, fs):
                p.outcome, hrv = run_hazard(llm, msg.text, p.outcome, fs)
                p.hazard_review = hrv.model_dump(mode="json")
        from palladion.agents.credibility import review_credibility, signals
        sigs = signals(msg.text, intake, p.location.status, p.minutes_to_front, p.outcome.flags)
        if sigs:
            crv = review_credibility(llm, msg.text, sigs)
            p.credibility = crv.model_dump(mode="json")
            if crv.assessment != "credible":
                p.outcome.flags.append(f"credibility_{crv.assessment}")
                p.outcome.reasons.append(f"Αξιοπιστία: {crv.reason_el}")
        from palladion.agents.needs import read as read_needs
        p.needs = read_needs(llm, msg.text, hint=bool(intake.output.vulnerable))
        from palladion.pipeline.arbiter import arbitrate
        p.arbiter = arbitrate(p.outcome, p.location.flags, p.credibility, p.hazard_review)
    return p


def finish(msg: Message, intake: IntakeResult, gaz: Gazetteer, fire: FireEngine,
           bbox: tuple[float, float, float, float], loc: Location | None = None) -> Processed:
    if loc is None:
        loc = operator_location(msg.point, gaz) if msg.point else locate(intake.output, gaz, bbox, msg.text)
    minutes, nearby = _front(loc, fire, msg.time)
    derived = None
    o = intake.output
    if o.people is None or o.people.count is None:
        # "είμαι με τα δύο μωρά μου" = 3: counted by code from the people the sender lists, to confirm.
        from palladion.agents.intake import People
        from palladion.pipeline.derive_count import derive
        derived = derive(msg.text, o.reporter.value)
        if derived:
            o.people = People(count=derived[0], quote=derived[1])
    outcome = decide(intake, loc, fire_facts(minutes, intake, nearby), msg.time, msg.text)
    if derived:
        outcome.flags.append("people_derived")
        outcome.reasons.append(f"Άτομα: {derived[0]}, μετρημένα από «{derived[1]}» μαζί με τον αποστολέα· "
                               "επιβεβαίωσε τον αριθμό.")
    from palladion.agents.needs import read as read_needs
    return Processed(msg, intake, loc, minutes, outcome, needs=read_needs(None, msg.text), front_nearby_m=nearby)


def _minutes(loc: Location, fire: FireEngine, t: datetime) -> float | None:
    return _front(loc, fire, t)[0]


def _front(loc: Location, fire: FireEngine, t: datetime) -> tuple[float | None, float | None]:
    """Minutes from `t` to the front at the location, and how far from the location's point that time was measured
    (None: at the point itself). A street stretch or an area is not a point: when the point itself is not reached,
    the earliest arrival within its extent counts (safety first), and the distance says so."""
    if loc.lon is None or loc.lat is None:
        return None, None
    arr = fire.arrival_time(loc.lon, loc.lat)
    nearby = None
    if arr is None and loc.uncertainty_m:
        hit = _earliest_nearby(fire, loc.lon, loc.lat, min(loc.uncertainty_m, 600))
        if hit is not None:
            arr, nearby = hit
            # Said only for a street whose point is on land that never burns (Δάσους, Αγία Μαρίνα): a point at the
            # shore or an area's centre is not a place the sender gave, and the time is the area's, as before.
            ground = getattr(fire, "ground", None)
            import shapely
            if loc.status != "resolved" or ground is None or not shapely.contains_xy(ground, loc.lon, loc.lat):
                nearby = None
    return (None, None) if arr is None else ((arr - t) / timedelta(minutes=1), nearby)


def _earliest_nearby(fire: FireEngine, lon: float, lat: float, radius_m: float) -> tuple[datetime, float] | None:
    """The earliest arrival within `radius_m` of the point, and the distance of the place where it happens."""
    import numpy as np

    from palladion.geo.proj import LocalFrame
    f = LocalFrame(lon, lat)
    ang = np.linspace(0, 2 * np.pi, 16, endpoint=False)
    rings = [radius_m / 4, radius_m / 2, 3 * radius_m / 4, radius_m]
    dist = [r for r in rings for _ in ang]
    pts = [f.to_lonlat(r * np.cos(a), r * np.sin(a)) for r in rings for a in ang]
    secs = fire.arrival_seconds(np.array([p[0] for p in pts]), np.array([p[1] for p in pts]))
    i = int(np.argmin(secs))
    s = float(secs[i])
    return None if not np.isfinite(s) else (fire.t0 + timedelta(seconds=s), float(dist[i]))
