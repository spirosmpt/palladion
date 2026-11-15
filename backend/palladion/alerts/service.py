"""112 desk: warnings proposed by code, written by the agent, sent only by a person.

Every ASSESS_EVERY minutes of scenario time the warning engine looks 30 minutes ahead. Each threatened
(settlement, time band) with its instruction becomes a proposal. The id is derived from settlement,
band and instruction, so the same warning is never proposed twice, and a changed instruction (for
example "leave by car" turning into "walk to the coast") is a new proposal that replaces the old one.
An operator can also ask for a warning for any point on the map.

Registry stream per warning: alert.proposed (agent), alert.edited / alert.sent / alert.dismissed (human).
"""
import hashlib
import queue
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any

from palladion.agents.alert_writer import MAX_CHARS, AlertText, WrittenAlert, check, write_alert
from palladion.engines.registry.registry import Actor, ActorKind, Registry
from palladion.engines.warnings import Threat, WarningEngine, nearest_settlement
from palladion.llm.client import LLM

ASSESS_EVERY = timedelta(minutes=5)
AGENT = Actor(kind=ActorKind.agent, id="alert_writer")


class AlertStatus(StrEnum):
    writing = "writing"
    proposed = "proposed"
    sent = "sent"
    dismissed = "dismissed"
    expired = "expired"          # the front reached the area before anyone acted
    replaced = "replaced"        # a newer proposal for the same area has a different instruction


OPEN = {AlertStatus.writing, AlertStatus.proposed}


@dataclass
class Alert:
    id: str
    threat: Threat
    created: datetime
    origin: str                                  # auto | operator
    status: AlertStatus = AlertStatus.writing
    written: WrittenAlert | None = None
    text: AlertText | None = None                # what will be / was sent (written or edited)
    edited: bool = False
    operator: str | None = None
    acted_at: datetime | None = None
    replaces: list[str] = field(default_factory=list)

    @property
    def area_key(self) -> str:
        return self.threat.key


class AlertError(Exception):
    pass


def alert_id(t: Threat) -> str:
    raw = f"{t.key}|{t.instruction.action.value}|{t.instruction.destination}"
    return "W" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:6]


class AlertService:
    def __init__(self, engine: WarningEngine, llm: LLM | None, registry: Registry):
        self.engine, self.llm, self.registry = engine, llm, registry
        self.places = [s.name for s in engine.settlements]
        self._alerts: dict[str, Alert] = {}
        self._lock = threading.RLock()
        self._last: datetime | None = None
        self._queue: queue.Queue[str] = queue.Queue()
        self._stop = threading.Event()

    # -- proposals -------------------------------------------------------------------------------------
    def tick(self, now: datetime, sync: bool = False) -> None:
        if self._last is not None and timedelta(0) <= now - self._last < ASSESS_EVERY:
            return
        self._last = now
        threats = self.engine.assess(now)
        current = {t.key for t in threats}
        with self._lock:
            for a in self._alerts.values():
                if a.status in OPEN and a.origin == "auto" and a.area_key not in current:
                    a.status = AlertStatus.expired
        for t in threats:
            self._propose(t, now, "auto", sync)

    def request(self, lon: float, lat: float, now: datetime, operator: str, sync: bool = False) -> Alert:
        """An operator asks for a warning at a point: its settlement, looking an hour ahead."""
        st = nearest_settlement(self.engine.settlements, lon, lat)
        if st is None:
            raise AlertError("Δεν υπάρχει οικισμός κοντά σε αυτό το σημείο.")
        threats = self.engine.assess(now, horizon_min=60, only=st.name, min_buildings=1)
        if not threats:
            raise AlertError(f"Το μέτωπο δεν φτάνει σε κτίρια της περιοχής {st.name} την επόμενη ώρα.")
        return self._propose(threats[0], now, "operator", sync, operator)

    def _propose(self, t: Threat, now: datetime, origin: str, sync: bool, operator: str | None = None) -> Alert:
        aid = alert_id(t)
        with self._lock:
            existing = self._alerts.get(aid)
            if existing is not None:
                if existing.status in OPEN:
                    existing.threat = t               # newer geometry and counts; same instruction
                return existing
            a = Alert(aid, t, now, origin)
            replaced = [x for x in self._alerts.values()
                        if x.area_key == t.key and x.id != aid and x.status in (OPEN | {AlertStatus.sent})]
            for x in replaced:
                a.replaces.append(x.id)
                if x.status in OPEN:
                    x.status = AlertStatus.replaced
            self._alerts[aid] = a
        actor = Actor(kind=ActorKind.human, id=operator) if operator else AGENT
        if self.registry.version(f"alert:{aid}") == 0:
            self.registry.append(f"alert:{aid}", "alert.proposed", actor, self._facts(a), expected_version=0,
                                 sim_time=now)
        if sync:
            self._write(aid)
        else:
            self._queue.put(aid)
        return a

    def _write(self, aid: str) -> None:
        with self._lock:
            a = self._alerts[aid]
            t = a.threat
        written = write_alert(self.llm, t, self.places)
        with self._lock:
            a.written = written
            if a.text is None:
                a.text = written.text
            if a.status == AlertStatus.writing:
                a.status = AlertStatus.proposed
        self._restore(aid)

    def start(self, clock_now) -> None:
        def ticker() -> None:
            while not self._stop.wait(1.0):
                try:
                    self.tick(clock_now())
                except Exception as exc:  # noqa: BLE001 - the desk must keep running
                    print("alert tick failed:", exc)

        def worker() -> None:
            while not self._stop.is_set():
                try:
                    aid = self._queue.get(timeout=0.5)
                except queue.Empty:
                    continue
                self._write(aid)

        threading.Thread(target=ticker, daemon=True, name="alert-ticker").start()
        threading.Thread(target=worker, daemon=True, name="alert-worker").start()

    def stop(self) -> None:
        self._stop.set()

    # -- human actions -------------------------------------------------------------------------------------
    def act(self, aid: str, action: str, operator: str, now: datetime, text: AlertText | None = None) -> Alert:
        with self._lock:
            a = self._alerts.get(aid)
            if a is None:
                raise AlertError(f"unknown warning {aid}")
            if a.status == AlertStatus.writing:
                raise AlertError("Η προειδοποίηση γράφεται ακόμα.")
            human = Actor(kind=ActorKind.human, id=operator or "operator")
            stream = f"alert:{aid}"
            if action == "edit":
                if a.status != AlertStatus.proposed or text is None:
                    raise AlertError("Αλλάζει μόνο κείμενο προειδοποίησης που δεν έχει σταλεί.")
                problems = check(text, a.threat, self.places)
                if problems:
                    raise AlertError("; ".join(problems))
                self.registry.append(stream, "alert.edited", human, text.model_dump(),
                                     expected_version=self.registry.version(stream), sim_time=now)
                a.text, a.edited = text, True
            elif action == "send":
                if a.status != AlertStatus.proposed or a.text is None:
                    raise AlertError("Στέλνεται μόνο προτεινόμενη προειδοποίηση.")
                self.registry.append(stream, "alert.approved", human, {},
                                     expected_version=self.registry.version(stream), sim_time=now)
                self.registry.append(stream, "alert.sent", human, {**a.text.model_dump(), **self._facts(a)},
                                     expected_version=self.registry.version(stream), sim_time=now)
                a.status = AlertStatus.sent
            elif action == "dismiss":
                if a.status not in OPEN:
                    raise AlertError("Η προειδοποίηση δεν είναι ανοιχτή.")
                self.registry.append(stream, "alert.dismissed", human, {},
                                     expected_version=self.registry.version(stream), sim_time=now)
                a.status = AlertStatus.dismissed
            else:
                raise AlertError(f"unknown action {action}")
            a.operator, a.acted_at = human.id, now
            return a

    def _restore(self, aid: str) -> None:
        """After a restart, what people did with this warning comes back from the registry."""
        with self._lock:
            a = self._alerts[aid]
            for ev in self.registry.events(f"alert:{aid}"):
                if ev.actor.kind is not ActorKind.human:
                    continue
                if ev.type == "alert.edited":
                    a.text, a.edited = AlertText(el=ev.data["el"], en=ev.data["en"]), True
                elif ev.type == "alert.sent":
                    a.text, a.status = AlertText(el=ev.data["el"], en=ev.data["en"]), AlertStatus.sent
                elif ev.type == "alert.dismissed":
                    a.status = AlertStatus.dismissed
                else:
                    continue
                a.operator, a.acted_at = ev.actor.id, ev.sim_time

    # -- reads -------------------------------------------------------------------------------------------------
    def get(self, aid: str) -> Alert | None:
        with self._lock:
            return self._alerts.get(aid)

    def visible(self) -> list[Alert]:
        with self._lock:
            alerts = list(self._alerts.values())
        rank = {AlertStatus.writing: 0, AlertStatus.proposed: 0, AlertStatus.sent: 1}
        return sorted(alerts, key=lambda a: (rank.get(a.status, 2), a.threat.first_arrival))

    @staticmethod
    def _facts(a: Alert) -> dict[str, Any]:
        t, ins = a.threat, a.threat.instruction
        return {"settlement": t.settlement, "band": list(t.band), "buildings": t.buildings,
                "first_arrival": t.first_arrival.isoformat(), "action": ins.action.value,
                "destination": ins.destination, "roads": ins.roads, "origin": a.origin}


__all__ = ["Alert", "AlertError", "AlertService", "AlertStatus", "MAX_CHARS"]
