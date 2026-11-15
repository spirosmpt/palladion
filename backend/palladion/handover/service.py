"""SITREP / handover desk: the skeleton is rebuilt on every read; a person can save one, with a summary,
as a `handover.created` event in the registry (who, when, what was handed over)."""
import hashlib
import json
from collections.abc import Callable
from datetime import datetime
from typing import Any

from palladion.engines.registry.registry import Actor, ActorKind, Registry
from palladion.handover import agent
from palladion.handover import skeleton as S

STREAM = "handover"


class HandoverService:
    def __init__(self, registry: Registry, llm: Any, snapshot: Callable[[datetime], dict[str, Any]]):
        self.registry, self.llm, self._snapshot = registry, llm, snapshot

    def view(self, now: datetime) -> dict[str, Any]:
        sk = S.build(self._snapshot(now), now)
        return {**sk, "text": S.as_text(sk)}

    def create(self, now: datetime, operator: str, summary: bool = True, note: str = "") -> dict[str, Any]:
        sk = S.build(self._snapshot(now), now)
        summ = agent.summarize(self.llm if summary else None, sk)
        key = now.isoformat() + json.dumps(sk["counts"], sort_keys=True) + str(len(self.history(now)))
        hid = "ho-" + hashlib.sha1(key.encode()).hexdigest()[:10]
        data = {"id": hid, "now": now.isoformat(), "skeleton": sk, "summary": summ, "note": note,
                "text": S.as_text(sk, summ) + (f"\nΣημείωση: {note}\n" if note else "")}
        self.registry.append(STREAM, "handover.created", Actor(kind=ActorKind.human, id=operator or "operator"), data,
                             expected_version=self.registry.version(STREAM), sim_time=now)
        return {**data, "by": operator}

    def history(self, now: datetime) -> list[dict[str, Any]]:
        return [{**ev.data, "by": ev.actor.id} for ev in self.registry.events(STREAM)
                if ev.type == "handover.created" and ev.sim_time and ev.sim_time <= now]
