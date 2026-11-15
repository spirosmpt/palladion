"""Coast Guard desk: the open sea and shore cases, grouped by stretch of shore; the liaison agent drafts the request
for each group and ranks them (agents/coastguard.py); a person sends it.

  - Code groups the open cases whose proposal is a rescue from the sea, or whose sender is on a beach or in the water,
    when they are within GROUP_M of each other. A group is named after its beach (if mapped) or its most common place.
  - The agent runs in the background when the desk asks and the groups have changed since its last draft; its draft
    is logged once per set of groups (coastguard.drafted). Until it answers the desk shows the code's templates.
  - Sending is a person's act (coastguard.request_sent, human only), with the text as sent and the messages covered.
    A message that joins a group after its request was sent is shown as new, for an update.
  - The supervisor watches for sea messages with no request sent after a while (supervisor/detectors.coast_guard).
"""
import hashlib
import threading
from collections.abc import Callable
from datetime import datetime
from typing import Any

from palladion.engines.registry.registry import Actor, ActorKind, Registry
from palladion.geo.proj import haversine_m
from palladion.textclean import clean

STREAM = "coastguard"
GROUP_M = 800.0
WARN_NEAR_M = 1500.0
SEA = "SEA_RESCUE_CANDIDATE"
CLOSED = {"closed", "merged", "resolved"}
SHORE_EL = {"cliff": "γκρεμός", "beach": "παραλία", "gentle": "ομαλή ακτή", "unknown": "ακτή"}


class CoastGuardError(ValueError):
    pass


class CoastGuardService:
    def __init__(self, registry: Callable[[], Registry], cases: Callable[[], Any], alerts: Callable[[], Any],
                 coast: Any, drafter: Callable[..., dict[str, Any]] | None = None):
        """`drafter(groups, facts, template, code_order) -> dict`: the liaison agent (agents/coastguard.draft)."""
        self._registry, self._cases, self._alerts, self.coast = registry, cases, alerts, coast
        self._drafter = drafter
        self._lock = threading.Lock()
        self._asking: set[str] = set()

    # -- groups ------------------------------------------------------------------------------------------------------
    def sea_cases(self, now: datetime) -> list[Any]:
        out = []
        for c in self._cases().visible(now):
            p = c.processed
            if p is None or c.status.value in CLOSED or p.location.lon is None or p.location.lat is None:
                continue
            o = p.intake.output if p.intake else None
            at_sea = o is not None and o.situation is not None and o.situation.kind.value in ("sea", "beach")
            if (c.final_decision or p.outcome.decision.value) == SEA or at_sea:
                out.append(c)
        return sorted(out, key=lambda c: c.message.time)

    def groups(self, now: datetime) -> dict[str, dict[str, Any]]:
        from palladion.cases.service import minutes_left
        groups: list[list[Any]] = []
        for c in self.sea_cases(now):
            lo, la = c.processed.location.lon, c.processed.location.lat
            home = next((g for g in groups if any(haversine_m(lo, la, x.processed.location.lon,
                                                              x.processed.location.lat) <= GROUP_M for x in g)), None)
            if home is None:
                groups.append([c])
            else:
                home.append(c)
        out: dict[str, dict[str, Any]] = {}
        for i, g in enumerate(groups, 1):
            lon = sum(c.processed.location.lon for c in g) / len(g)
            lat = sum(c.processed.location.lat for c in g) / len(g)
            mins = [m for m in (minutes_left(c, now) for c in g) if m is not None]
            out[f"G{i}"] = {"id": f"G{i}", "label": self._label(g, lon, lat), "lon": lon, "lat": lat,
                            "case_ids": [c.message.id for c in g], "first": g[0].message.time,
                            "front_min": round(min(mins)) if mins else None}
        return out

    def _label(self, g: list[Any], lon: float, lat: float) -> str:
        acc = self.coast.nearest_access(lon, lat) if self.coast is not None else None
        if acc is not None and acc.name and acc.distance_m <= 800:
            return str(acc.name)
        labels = [c.processed.location.label for c in g if c.processed.location.label]
        return max(set(labels), key=labels.count) if labels else "ακτή"

    @staticmethod
    def code_order(groups: dict[str, dict[str, Any]]) -> list[str]:
        """Earliest front first; then more messages."""
        return sorted(groups, key=lambda k: (groups[k]["front_min"] if groups[k]["front_min"] is not None else 10**6,
                                             -len(groups[k]["case_ids"])))

    # -- what the tools return ---------------------------------------------------------------------------------------
    def facts(self, now: datetime, groups: dict[str, dict[str, Any]]) -> Callable[[str, str], dict[str, Any]]:
        from palladion.cases.service import minutes_left
        cases = {c.message.id: c for c in self.sea_cases(now)}

        def run(tool: str, gid: str) -> dict[str, Any]:
            g = groups[gid]
            if tool == "group_cases":
                rows = []
                for cid in g["case_ids"]:
                    c = cases[cid]
                    p = c.processed
                    o = p.intake.output if p.intake else None
                    rows.append({"id": cid, "time": c.message.time.strftime("%H:%M"),
                                 "people": self._cases().people(cid),
                                 "where": o.situation.quote if o and o.situation else p.location.label,
                                 "place": p.location.label,
                                 "care": [v.kind.value for v in o.vulnerable] if o else []})
                known = [r["people"] for r in rows if r["people"]]
                return {"group": gid, "cases": rows, "people_known": sum(known),
                        "messages_without_count": sum(1 for r in rows if not r["people"])}
            if tool == "shore":
                if self.coast is None:
                    return {"group": gid, "note": "no shore data"}
                hit = self.coast.nearest_shore(g["lon"], g["lat"])
                acc = self.coast.nearest_access(g["lon"], g["lat"])
                out: dict[str, Any] = {"group": gid}
                if hit is not None:
                    out["shore"] = SHORE_EL.get(str(hit[0].value), str(hit[0].value))
                    out["shore_m"] = round(hit[1] / 10) * 10
                out["cliff_within_150m"] = bool(self.coast.near_cliff(g["lon"], g["lat"]))
                if acc is not None:
                    out["boat_access"] = {"name": acc.name or "", "kind": SHORE_EL.get(str(acc.kind.value), ""),
                                          "m": round(acc.distance_m / 10) * 10}
                return out
            if tool == "fire":
                per = {cid: (None if (m := minutes_left(cases[cid], now)) is None else round(m))
                       for cid in g["case_ids"]}
                vals = [m for m in per.values() if m is not None]
                return {"group": gid, "front_min_earliest": min(vals) if vals else None, "per_message": per,
                        "note": "negative: the front has already passed there"}
            if tool == "warnings_sent":
                al = self._alerts()
                rows = []
                for a in (al.visible() if al else []):
                    ins = a.threat.instruction
                    if a.status.value != "sent" or ins.action.value != "go_to_coast" or not ins.coast_point \
                            or not a.acted_at or a.acted_at > now:
                        continue
                    d = haversine_m(g["lon"], g["lat"], ins.coast_point[0], ins.coast_point[1])
                    if d <= WARN_NEAR_M:
                        rows.append({"id": a.id, "area": a.threat.settlement, "sent": a.acted_at.strftime("%H:%M"),
                                     "buildings": a.threat.buildings, "m_from_group": round(d / 10) * 10})
                return {"group": gid, "warnings": rows}
            return {"error": "unknown tool"}
        return run

    def template(self, now: datetime, groups: dict[str, dict[str, Any]]) -> Callable[[str], str]:
        facts = self.facts(now, groups)

        def text(gid: str) -> str:
            g, cs, sh = groups[gid], facts("group_cases", gid), facts("shore", gid)
            parts = [f"Αίτημα προς Λιμενικό: {g['label']}."]
            n = len(g["case_ids"])
            ppl = f"δηλωμένα άτομα {cs['people_known']}" if cs["people_known"] else "χωρίς αριθμό ατόμων"
            extra = f", {cs['messages_without_count']} μηνύματα χωρίς αριθμό" if cs["people_known"] and \
                cs["messages_without_count"] else ""
            parts.append(f"{n} {'μήνυμα' if n == 1 else 'μηνύματα'} ({', '.join(g['case_ids'])}), {ppl}{extra}.")
            if sh.get("shore"):
                cliff = " Γκρεμός σε απόσταση έως 150 m: πρόσβαση μόνο από θάλασσα." if sh.get("cliff_within_150m") \
                    else ""
                parts.append(f"Ακτή: {sh['shore']}.{cliff}")
            if g["front_min"] is not None:
                parts.append(f"Μέτωπο σε {g['front_min']}′." if g["front_min"] > 0
                             else "Το μέτωπο έχει ήδη περάσει από εκεί.")
            return " ".join(parts)
        return text

    # -- drafts ------------------------------------------------------------------------------------------------------
    @staticmethod
    def key(groups: dict[str, dict[str, Any]]) -> str:
        raw = "|".join(f"{k}:{','.join(g['case_ids'])}" for k, g in sorted(groups.items()))
        return hashlib.sha1(raw.encode()).hexdigest()[:12]

    def _drafts(self) -> dict[str, dict[str, Any]]:
        return {ev.data["key"]: {**ev.data, "at": ev.sim_time} for ev in self._registry().events(STREAM)
                if ev.type == "coastguard.drafted"}

    def draft_now(self, now: datetime) -> dict[str, Any]:
        """The liaison agent on the current groups (synchronous); logged once per set of groups."""
        groups = self.groups(now)
        k = self.key(groups)
        res = self._drafter(groups, self.facts(now, groups), self.template(now, groups), self.code_order(groups)) \
            if self._drafter else {"requests": {}, "status": "code", "order": self.code_order(groups)}
        data = {"key": k, "groups": {g: v["case_ids"] for g, v in groups.items()}, **res}
        reg = self._registry()
        with self._lock:
            if not any(ev.type == "coastguard.drafted" and ev.data.get("key") == k for ev in reg.events(STREAM)):
                reg.append(STREAM, "coastguard.drafted", Actor(kind=ActorKind.agent, id="coastguard"), data,
                           expected_version=reg.version(STREAM), sim_time=now)
        return data

    def _draft_later(self, k: str, now: datetime) -> None:
        with self._lock:
            if k in self._asking:
                return
            self._asking.add(k)

        def go() -> None:
            try:
                self.draft_now(now)
            except Exception as exc:  # noqa: BLE001 - the templates stay
                print("coast guard agent:", exc)
        threading.Thread(target=go, daemon=True, name="coastguard").start()

    # -- the desk ----------------------------------------------------------------------------------------------------
    def sent(self, now: datetime) -> list[dict[str, Any]]:
        return [{**ev.data, "at": ev.sim_time.isoformat(), "by": ev.actor.id} for ev in self._registry().events(STREAM)
                if ev.type == "coastguard.request_sent" and ev.sim_time is not None and ev.sim_time <= now]

    def view(self, now: datetime, ask: bool = False) -> dict[str, Any]:
        groups = self.groups(now)
        k = self.key(groups)
        draft = self._drafts().get(k)
        if draft is None and groups and ask and self._drafter is not None:
            self._draft_later(k, now)
        template = self.template(now, groups)
        sent = self.sent(now)
        order = (draft or {}).get("order") or self.code_order(groups)
        rows = []
        for rank, gid in enumerate([g for g in order if g in groups], 1):
            g = groups[gid]
            req = ((draft or {}).get("requests") or {}).get(gid) or \
                {"text": template(gid), "cites": g["case_ids"], "why": "", "source": "template"}
            covered = {c for s in sent for c in s.get("case_ids", [])}
            mine = [s for s in sent if set(s.get("case_ids", [])) & set(g["case_ids"])]
            rows.append({**{x: v for x, v in g.items() if x != "first"}, "first": g["first"].isoformat(),
                         "rank": rank, "request": req, "sent": mine[-1] if mine else None,
                         "new_since_sent": [c for c in g["case_ids"] if c not in covered] if mine else []})
        return {"now": now.isoformat(), "groups": rows,
                "agent": None if draft is None else {"status": draft.get("status"), "order_by": draft.get("order_by"),
                                                     "steps": draft.get("steps", []),
                                                     "rejected": draft.get("rejected", []),
                                                     "calls": draft.get("calls", []), "error": draft.get("error")},
                "pending": bool(groups) and draft is None and self._drafter is not None,
                "waiting_since": self.waiting_since(now, groups, sent)}

    def waiting_since(self, now: datetime, groups: dict[str, dict[str, Any]] | None = None,
                      sent: list[dict[str, Any]] | None = None) -> str | None:
        """The earliest sea message no request has covered yet (for the supervisor)."""
        groups = self.groups(now) if groups is None else groups
        covered = {c for s in (self.sent(now) if sent is None else sent) for c in s.get("case_ids", [])}
        times = [c.message.time for c in self.sea_cases(now) if c.message.id not in covered]
        return min(times).isoformat() if times else None

    def send(self, gid: str, text: str, operator: str, now: datetime) -> dict[str, Any]:
        groups = self.groups(now)
        g = groups.get(gid)
        if g is None:
            raise CoastGuardError("no such group now")
        text = clean(text).strip()
        if not 10 <= len(text) <= 1500:
            raise CoastGuardError("το αίτημα θέλει 10 έως 1500 χαρακτήρες")
        reg = self._registry()
        reg.append(STREAM, "coastguard.request_sent", Actor(kind=ActorKind.human, id=operator or "operator"),
                   {"group": gid, "label": g["label"], "case_ids": g["case_ids"], "text": text},
                   expected_version=reg.version(STREAM), sim_time=now)
        return self.view(now)
