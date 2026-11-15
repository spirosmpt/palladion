from datetime import timedelta

import pytest

from palladion.engines.registry.registry import Actor, ActorKind, Forbidden, Registry, VersionConflict
from palladion.scenarios.clock import ScenarioClock

from .conftest import T0

HUMAN = Actor(kind=ActorKind.human, id="operator-1")
AGENT = Actor(kind=ActorKind.agent, id="intake")


def test_append_and_versions(tmp_path):
    r = Registry(tmp_path / "ev.jsonl")
    r.append("case:1", "case.proposed", AGENT, {"p": 1}, expected_version=0)
    r.append("case:1", "case.approved", HUMAN, expected_version=1)
    assert r.version("case:1") == 2
    with pytest.raises(VersionConflict):
        r.append("case:1", "case.note", AGENT, expected_version=1)


def test_agents_cannot_commit_actions(tmp_path):
    r = Registry(tmp_path / "ev.jsonl")
    r.append("case:1", "case.proposed", AGENT, expected_version=0)
    with pytest.raises(Forbidden):
        r.append("case:1", "case.approved", AGENT, expected_version=1)


def test_log_survives_restart_and_detects_tampering(tmp_path):
    p = tmp_path / "ev.jsonl"
    r = Registry(p)
    r.append("case:1", "case.proposed", AGENT, {"people": 3}, expected_version=0, sim_time=T0)
    r.append("case:2", "case.proposed", AGENT, {"people": 1}, expected_version=0)
    assert [e.stream for e in Registry(p).events()] == ["case:1", "case:2"]
    p.write_text(p.read_text(encoding="utf-8").replace('"people":3', '"people":30'), encoding="utf-8")
    with pytest.raises(ValueError, match="corrupted"):
        Registry(p)


class FakeWall:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def test_clock_play_pause_speed_seek():
    wall = FakeWall()
    c = ScenarioClock(T0, T0 + timedelta(hours=4), speed=60, wall=wall)
    c.play()
    wall.t += 10                                  # 10 s wall at 60x = 10 scenario minutes
    assert c.state().sim_time == T0 + timedelta(minutes=10)
    c.set_speed(6)
    wall.t += 10
    assert c.state().sim_time == T0 + timedelta(minutes=11)
    c.pause()
    wall.t += 100
    assert c.state().sim_time == T0 + timedelta(minutes=11)
    c.seek(T0 + timedelta(hours=10))
    assert c.state().sim_time == T0 + timedelta(hours=4)
    c.play()                                      # play at the end restarts from the beginning
    assert c.state().sim_time == T0


def test_clock_stops_at_end():
    wall = FakeWall()
    c = ScenarioClock(T0, T0 + timedelta(minutes=30), speed=60, wall=wall)
    c.play()
    wall.t += 3600
    s = c.state()
    assert s.sim_time == T0 + timedelta(minutes=30) and not s.playing
