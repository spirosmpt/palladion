"""Citizen-reply simulator for demo and evaluation ONLY.

Replies come from a hidden truth record per synthetic message (scenarios/<id>/callback_truth.yaml),
never from the wording of the questions. Every reply is marked as simulated. A message without a
record (every real message) never gets a reply.
"""
from dataclasses import dataclass
from pathlib import Path

import yaml

PREFIX = "[ΠΡΟΣΟΜΟΙΩΣΗ] "


@dataclass
class Reply:
    delay_min: float
    text: str


class Simulator:
    def __init__(self, records: dict[str, Reply]):
        self.records = records

    @classmethod
    def load(cls, scenario_dir: Path) -> "Simulator":
        path = scenario_dir / "callback_truth.yaml"
        if not path.exists():
            return cls({})
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return cls({k: Reply(float(v.get("delay_min", 5)), str(v["reply"])) for k, v in raw.items()})

    def reply(self, message_id: str) -> Reply | None:
        return self.records.get(message_id)
