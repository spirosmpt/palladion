"""Crew simulator for the demo and evaluation ONLY. Every report it sends is marked as simulated.

In a real desk the crews report by radio and someone types it, or a crew app sends it. In the demo
there are no crews, so this simulator plays them: it follows the plan the operator approved and says
"we left", "we arrived" at the estimated times. What the crew finds comes from a hidden record per
case (scenarios/<id>/field_truth.yaml), never from the triage, so it can disagree with it. A case with
no record gets no outcome report: the unit stays on scene until an operator types what the crew said.
"""
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from palladion.field.service import FieldError, FieldService


@dataclass
class Step:
    key: str
    unit: str
    mission: str
    at: datetime
    text: str
    agent: bool = False                 # read by the agent too (outcome reports); status lines by rules only


class CrewSimulator:
    def __init__(self, field: FieldService, truth: dict[str, str],
                 land: Callable[[], list[Any]] = lambda: []):
        self.field, self.truth = field, truth
        self._land = land

    @classmethod
    def load(cls, scenario_dir: Path, field: FieldService, **kw: Any) -> "CrewSimulator":
        p = scenario_dir / "field_truth.yaml"
        raw = yaml.safe_load(p.read_text(encoding="utf-8")) if p.exists() else {}
        return cls(field, {k: str(v["report"]) for k, v in (raw or {}).items()}, **kw)

    def steps(self) -> list[Step]:
        out: list[Step] = []
        for m in self._land():
            k = m.id
            out += [Step(f"{k}:dep", m.unit, k, m.depart, "Φύγαμε από τη βάση."),
                    Step(f"{k}:arr", m.unit, k, m.arrive, "Φτάσαμε στο σημείο.")]
            if m.case_id in self.truth:
                out += [Step(f"{k}:out", m.unit, k, m.leave_scene, self.truth[m.case_id] + " Επιστρέφουμε στη βάση.",
                             agent=True),
                        Step(f"{k}:free", m.unit, k, m.back, "Διαθέσιμοι στη βάση.")]
        return out

    def tick(self, now: datetime) -> int:
        sent = 0
        for s in sorted(self.steps(), key=lambda s: s.at):
            if s.at > now or self.field.simulated(s.key):
                continue
            a = self.field.assignment(s.mission)
            if a is None or (a.cancelled_at is not None and a.cancelled_at <= s.at):
                continue
            text = s.text
            try:
                self.field.report(s.unit, text, s.at, source="crew", actor="crew-simulator", simulated=True,
                                  mission=s.mission, use_agent=s.agent, sim_key=s.key)
                sent += 1
            except FieldError:
                continue
        return sent
