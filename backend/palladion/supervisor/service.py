"""Supervisor: every 10 scenario minutes, look at the whole desk, raise what is slipping, and check back.

  tick       code finds (detectors.py); findings already raised and not yet resolved are not raised again,
             nor are ones a person dismissed (unless new cases join them); the agent picks and words at most
             three (agent.py); each alert gets an owner, a due time and a closing condition from code.
  re-check   at every tick, each open or accepted alert is checked against what code finds now: resolved
             when the condition is gone for its cases or units, still open otherwise, escalated once when it
             is still open after its due time.
  decide     a person accepts (the action runs: open the case, ask for a 112 proposal, alert the Coast
             Guard...) or dismisses. Accepting is the human decision: nothing the agent says runs by itself,
             and no action here sends a unit or a boat.
Everything is an event in the registry: `supervisor.tick` (system) and `supervisor.decided` (human only).
"""
import hashlib
import threading
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

from palladion.engines.registry.registry import Actor, ActorKind, Registry
from palladion.supervisor import detectors as D
from palladion.supervisor.agent import rank

STREAM = "supervisor"
SYSTEM = Actor(kind=ActorKind.system, id="supervisor")
PER_SUBJECT = {"unit_silent", "unit_idle", "unit_down", "coast_guard"}


class SupervisorError(Exception):
    pass


class SupervisorService:
    def __init__(self, registry: Registry, llm: Any, snapshot: Callable[[datetime], dict[str, Any]],
                 actions: dict[str, Callable[[dict[str, Any], str, datetime], dict[str, Any]]] | None = None,
                 cfg: D.Config | None = None):
        self.registry, self.llm, self._snapshot = registry, llm, snapshot
        self.actions = actions or {}
        self.cfg = cfg or D.Config()
        self._lock = threading.RLock()

    # -- state, rebuilt from the registry -------------------------------------------------------------
    def state(self) -> dict[str, Any]:
        ticks: list[dict[str, Any]] = []
        alerts: dict[str, dict[str, Any]] = {}
        for ev in self.registry.events(STREAM):
            d = ev.data
            if ev.type == "supervisor.tick":
                ticks.append(d)
                for a in d["alerts"]:
                    alerts[a["alert_id"]] = {**a, "status": "open", "outcome": None, "rechecks": [], "decision": None,
                                             "escalated": False}
                for r in d["rechecks"]:
                    a = alerts.get(r["alert_id"])
                    if a is None:
                        continue
                    a["outcome"] = r["outcome"]
                    a["rechecks"].append({**r, "at": d["now"]})
                    if r["outcome"] == "resolved":
                        a.update(status="closed", closed_at=d["now"])
                    if r.get("escalated"):
                        a.update(escalated=True, severity=a["severity"] + 10)
            elif ev.type == "supervisor.decided":
                a = alerts.get(d["alert_id"])
                if a is not None and a["status"] == "open":
                    a["status"] = "accepted" if d["decision"] == "accept" else "dismissed"
                    a["decision"] = {"decision": d["decision"], "by": ev.actor.id, "at": d["at"],
                                     "result": d.get("result")}
        return {"ticks": ticks, "alerts": alerts}

    # -- re-check -------------------------------------------------------------------------------------
    @staticmethod
    def recheck(a: dict[str, Any], current: dict[str, dict[str, Any]], now: datetime,
                undecided: set[str] | None = None) -> dict[str, Any]:
        cur = current.get(a["finding_id"])
        if a["kind"] == "cluster":
            # A cluster is about cases nobody has decided yet: it ends when they are decided, not when the
            # 20-minute window moves past them.
            orig = set(a["case_ids"])
            left_set = orig & (undecided or set())
            out = {"alert_id": a["alert_id"], "outcome": "still_open" if left_set else "resolved",
                   "remaining_case_ids": sorted(left_set), "before": len(orig), "after": len(left_set),
                   "text_el": f"χωρίς απόφαση: {len(orig)} → {len(left_set)}"}
            if left_set and not a.get("escalated") and now > datetime.fromisoformat(a["due_at"]):
                out["escalated"] = True
            return out
        if a["kind"] in PER_SUBJECT:
            left_cases = a["case_ids"] if cur else []
            left = 1 if cur else 0
            before = 1
            text = "η συνθήκη ισχύει ακόμα" if cur else "η συνθήκη έπαψε (νέα αναφορά ή απόφαση)"
        else:
            subjects = "case_ids"
            orig = set(a[subjects])
            now_in = set(cur[subjects]) if cur else set()
            left_set = orig & now_in
            left_cases = sorted(left_set) if subjects == "case_ids" else []
            before, left = len(orig), len(left_set)
            text = f"στο εύρημα: {before} → {left}"
        out = {"alert_id": a["alert_id"], "outcome": "still_open" if left else "resolved",
               "remaining_case_ids": left_cases, "before": before, "after": left, "text_el": text}
        if left and not a.get("escalated") and now > datetime.fromisoformat(a["due_at"]):
            out["escalated"] = True
        return out

    # -- tick -----------------------------------------------------------------------------------------
    def last_tick(self) -> datetime | None:
        ticks = [ev.sim_time for ev in self.registry.events(STREAM) if ev.type == "supervisor.tick" and ev.sim_time]
        return max(ticks) if ticks else None

    def maybe_tick(self, now: datetime) -> dict[str, Any] | None:
        last = self.last_tick()
        if last is not None and now < last + timedelta(minutes=self.cfg.interval_min):
            return None
        return self.tick(now)

    def tick(self, now: datetime) -> dict[str, Any]:
        with self._lock:
            st = self.state()
            snap = self._snapshot(now)
            findings = D.detect(snap, now, self.cfg)
            current = {f["id"]: f for f in findings}
            undecided = {c["id"] for c in snap["cases"] if c["status"] in ("proposed", "failed")}
            rechecks = [self.recheck(a, current, now, undecided) for a in st["alerts"].values()
                        if a["status"] in ("open", "accepted") and a["outcome"] != "resolved"]
            resolved_now = {r["alert_id"] for r in rechecks if r["outcome"] == "resolved"}
            blocked = {a["finding_id"] for a in st["alerts"].values()
                       if a["status"] in ("open", "accepted") and a["outcome"] != "resolved"
                       and a["alert_id"] not in resolved_now}
            live_clusters = [set(a["case_ids"]) for a in st["alerts"].values()
                             if a["kind"] == "cluster" and a["status"] in ("open", "accepted")
                             and a["alert_id"] not in resolved_now]
            fresh = []
            for f in findings:
                if f["id"] in blocked:
                    continue
                if f["kind"] == "cluster" and any(len(set(f["case_ids"]) & c) * 2 >= len(f["case_ids"])
                                                  for c in live_clusters):
                    continue                          # mostly the same cases as a cluster already raised
                dismissed = [set(a["case_ids"]) | set(a["unit_ids"]) for a in st["alerts"].values()
                             if a["finding_id"] == f["id"] and a["status"] == "dismissed"]
                if any(set(f["case_ids"]) | set(f["unit_ids"]) <= d for d in dismissed):
                    continue                          # dismissed, and nothing new joined it
                fresh.append(f)
            ranked = rank(self.llm, fresh)
            tick_id = "t-" + hashlib.sha1(f"{now.isoformat()}#{len(st['ticks'])}".encode()).hexdigest()[:10]
            alerts = []
            for r in ranked["alerts"]:
                f = current[r["finding_id"]]
                alerts.append({
                    "alert_id": "sv-" + hashlib.sha1((tick_id + f["id"]).encode()).hexdigest()[:10], "tick_id": tick_id,
                    "created_at": now.isoformat(), "finding_id": f["id"], "kind": f["kind"], "why_el": r["why_el"],
                    "fact_el": f["text_el"], "action": r["action"], "action_el": D.ACTION_EL[r["action"]],
                    "allowed": f["allowed"], "owner_el": D.OWNER_EL[r["action"]],
                    "due_at": (now + timedelta(minutes=self.cfg.due_min)).isoformat(),
                    "case_ids": f["case_ids"], "unit_ids": f["unit_ids"], "landing_ids": f["landing_ids"],
                    "point": f["point"], "numbers": f["numbers"], "severity": f["severity"]})
            data = {"tick_id": tick_id, "now": now.isoformat(), "interval_min": self.cfg.interval_min,
                    "findings": [{k: f[k] for k in ("id", "kind", "case_ids", "unit_ids", "severity", "text_el")}
                                 for f in findings],
                    "alerts": alerts, "rechecks": rechecks,
                    "unalerted": [f["id"] for f in fresh if f["id"] not in {a["finding_id"] for a in alerts}],
                    "check": ranked["check"], "rejected": ranked["rejected"], "call": ranked["call"]}
            self.registry.append(STREAM, "supervisor.tick", SYSTEM, data,
                                 expected_version=self.registry.version(STREAM), sim_time=now)
            return data

    # -- a person decides ------------------------------------------------------------------------------
    def decide(self, alert_id: str, decision: str, operator: str, now: datetime,
               action: str | None = None) -> dict[str, Any]:
        if decision not in ("accept", "dismiss"):
            raise SupervisorError(f"unknown decision {decision}")
        with self._lock:
            a = self.state()["alerts"].get(alert_id)
            if a is None:
                raise SupervisorError("unknown alert")
            if a["status"] != "open":
                raise SupervisorError("Η ειδοποίηση έχει ήδη απόφαση ή έκλεισε.")
            act = action or a["action"]
            if act not in a["allowed"]:
                raise SupervisorError(f"η ενέργεια {act} δεν επιτρέπεται για αυτό το εύρημα")
            result = None
            if decision == "accept":
                fn = self.actions.get(act)
                try:
                    result = fn(a, operator, now) if fn else {"ok": True}
                except Exception as exc:  # noqa: BLE001 - recorded: the person sees what failed
                    result = {"ok": False, "error": str(exc)}
            self.registry.append(STREAM, "supervisor.decided", Actor(kind=ActorKind.human, id=operator or "operator"),
                                 {"alert_id": alert_id, "decision": decision, "action": act, "at": now.isoformat(),
                                  "result": result},
                                 expected_version=self.registry.version(STREAM), sim_time=now)
            return {"alert_id": alert_id, "decision": decision, "action": act, "result": result}

    # -- the desk ---------------------------------------------------------------------------------------
    def view(self, now: datetime) -> dict[str, Any]:
        st = self.state()
        alerts = [a for a in st["alerts"].values() if datetime.fromisoformat(a["created_at"]) <= now]
        order = {"open": 0, "accepted": 1, "dismissed": 2, "closed": 3}
        alerts.sort(key=lambda a: (order[a["status"]], -a["severity"], a["created_at"]))
        last = st["ticks"][-1] if st["ticks"] else None
        return {"now": now.isoformat(), "interval_min": self.cfg.interval_min,
                "last_tick": last["now"] if last else None, "last_check": last["check"] if last else None,
                "last_rejected": last["rejected"] if last else [],
                "unalerted": len(last["unalerted"]) if last else 0,
                "findings_now": len(last["findings"]) if last else 0,
                "alerts": alerts,
                "counts": {**{k: sum(a["status"] == k for a in alerts) for k in order},
                           "escalated": sum(bool(a.get("escalated")) for a in alerts
                                            if a["status"] in ("open", "accepted"))}}
