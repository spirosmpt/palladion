"""Event registry (the "world model"): append-only, versioned, human-gated.

Every change to a case, unit, mission or alert is one JSON line. Nothing is ever rewritten:
the current state is a projection of the log. Appends to one stream must name the version
they were based on, so two operators cannot silently overwrite each other. Some event types
are human-only: no agent or system actor can append them.
"""
import hashlib
import json
import threading
from collections.abc import Iterator
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


class ActorKind(StrEnum):
    human = "human"
    agent = "agent"
    system = "system"


class Actor(BaseModel):
    kind: ActorKind
    id: str


# Transitions that commit people, units or public messages to an action.
HUMAN_ONLY = frozenset({
    "case.approved", "case.decision_changed", "case.reopened", "case.merged", "case.unmerged", "case.closed",
    "case.linked", "case.unlinked", "case.not_duplicate",
    "mission.approved", "mission.cancelled",
    "alert.approved", "alert.sent", "alert.edited", "alert.dismissed",
    "unit.dispatched", "supervisor.decided", "coastguard.request_sent", "case.submitted",
    "callback.answered", "case.contradiction_decided", "case.sender_told",
})


class Event(BaseModel):
    seq: int
    stream: str                      # e.g. "case:0042", "unit:PY-12"
    version: int                     # 1-based position within the stream
    type: str
    actor: Actor
    sim_time: datetime | None = None
    recorded_at: datetime
    data: dict[str, Any] = Field(default_factory=dict)
    prev_hash: str
    hash: str


class VersionConflict(Exception):
    def __init__(self, stream: str, expected: int, actual: int):
        super().__init__(f"{stream}: expected version {expected}, found {actual}")
        self.stream, self.expected, self.actual = stream, expected, actual


class Forbidden(Exception):
    pass


def _digest(prev_hash: str, body: dict[str, Any]) -> str:
    payload = json.dumps(body, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256((prev_hash + payload).encode("utf-8")).hexdigest()


class Registry:
    """Thread-safe for one process. A multi-process deployment swaps in a database behind this API."""

    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._events: list[Event] = []
        self._versions: dict[str, int] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        prev = "genesis"
        for n, line in enumerate(self.path.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            ev = Event.model_validate_json(line)
            body = ev.model_dump(mode="json", exclude={"hash"})
            if ev.prev_hash != prev or _digest(prev, body) != ev.hash:
                # Fail closed: a damaged log must be repaired by a person, never guessed around.
                raise ValueError(f"registry log corrupted at line {n}")
            self._events.append(ev)
            self._versions[ev.stream] = ev.version
            prev = ev.hash

    def version(self, stream: str) -> int:
        with self._lock:
            return self._versions.get(stream, 0)

    def append(self, stream: str, type: str, actor: Actor, data: dict[str, Any] | None = None, *,
               expected_version: int, sim_time: datetime | None = None) -> Event:
        if type in HUMAN_ONLY and actor.kind is not ActorKind.human:
            raise Forbidden(f"'{type}' can only be recorded by a human operator")
        with self._lock:
            actual = self._versions.get(stream, 0)
            if actual != expected_version:
                raise VersionConflict(stream, expected_version, actual)
            prev = self._events[-1].hash if self._events else "genesis"
            ev = Event(seq=len(self._events) + 1, stream=stream, version=actual + 1, type=type, actor=actor,
                       sim_time=sim_time, recorded_at=datetime.now(UTC), data=data or {},
                       prev_hash=prev, hash="")
            # Hash the same canonical form that _load() recomputes, so the chain verifies on restart.
            ev.hash = _digest(prev, ev.model_dump(mode="json", exclude={"hash"}))
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(ev.model_dump_json() + "\n")
            self._events.append(ev)
            self._versions[stream] = ev.version
            return ev

    def events(self, stream: str | None = None, after_seq: int = 0) -> Iterator[Event]:
        with self._lock:
            snapshot = list(self._events)
        for ev in snapshot:
            if ev.seq > after_seq and (stream is None or ev.stream == stream):
                yield ev
