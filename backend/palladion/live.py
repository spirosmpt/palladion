"""Live messages from the public: anyone with the link writes to the centre from the public page.

For a demo in front of people: they play residents of the replay and send a message; it goes into the same queue,
through the same agents, and waits for the same person to decide. Nothing here decides anything.
  - The message enters at the replay's clock, channel "live_web", id "w01", "w02", ... It is marked as written by
    the public, not typed in by an operator.
  - A limit per sender (3 a minute) and in all (60 in ten minutes), so a crowd cannot flood the desk.
  - The sender sees only their own message: whether it was read, which agents read it and how long each took, what
    code proposes, and that a person decides. Never another message, never anything about other people.
"""
import threading
import time
from collections import deque
from collections.abc import Callable
from datetime import datetime
from typing import Any

from palladion.flow.service import DECISION_EL, NODE_EL

AGENTS = ("intake", "critic", "location", "hazard", "credibility", "needs", "duplicates", "callback")
DECISION_EN = {
    "EVACUATE_ROUTE_CANDIDATE": "evacuation", "DISPATCH_UNIT_CANDIDATE": "send a unit", "SHELTER_CANDIDATE": "shelter",
    "SEA_RESCUE_CANDIDATE": "rescue from the sea", "CALLBACK": "call back", "MISSING_PERSON": "missing person",
    "REPORTED_SAFE": "safe", "MONITOR": "monitor", "INFO": "information", "REVIEW_REQUIRED": "a person reviews it",
    "OUT_OF_AREA": "outside the area",
}
NODE_EN = {"intake": "Reader", "critic": "Critic", "location": "Location", "hazard": "Hazard",
           "credibility": "Credibility", "needs": "Needs", "duplicates": "Duplicates", "callback": "Call-back"}


class RateLimit:
    def __init__(self, per_client: int = 3, per_client_s: float = 60, total: int = 60, total_s: float = 600,
                 clock: Callable[[], float] = time.monotonic):
        self.per_client, self.per_client_s, self.total, self.total_s = per_client, per_client_s, total, total_s
        self._clock = clock
        self._lock = threading.Lock()
        self._by: dict[str, deque[float]] = {}
        self._all: deque[float] = deque()

    def allow(self, client: str) -> str | None:
        """None if the message may go in, else why not."""
        now = self._clock()
        with self._lock:
            while self._all and now - self._all[0] > self.total_s:
                self._all.popleft()
            mine = self._by.setdefault(client, deque())
            while mine and now - mine[0] > self.per_client_s:
                mine.popleft()
            if len(self._all) >= self.total:
                return "busy"
            if len(mine) >= self.per_client:
                return "too_fast"
            mine.append(now)
            self._all.append(now)
            return None


def status(case: Any, flow: dict[str, Any], now: datetime) -> dict[str, Any]:
    """What the sender of a live message may see about it."""
    p = case.processed
    steps: list[dict[str, Any]] = []
    tools = 0
    for e in flow.get("events", []):
        if e["src"] == "location" and e["kind"] == "tool_call":
            tools += 1
            continue
        # an agent's model call, or the location found by code alone (no model needed)
        if e["src"] in AGENTS and (e["kind"] == "call" or (e["src"] == "location" and e["kind"] == "data")):
            a = e["src"]
            steps.append({"agent": a, "el": NODE_EL.get(a, a), "en": NODE_EN.get(a, a), "status": e["status"],
                          "seconds": e["seconds"], "tools": tools if a == "location" else 0,
                          "note": (p.location_review or {}).get("outcome") if a == "location" and p else None})
    if p is None or case.status.value == "processing":
        steps = progress(case)                       # the reading under way: each agent as soon as it ends
    decision = case.final_decision or (p.outcome.decision.value if p else None)
    decided = case.status.value in ("approved", "changed", "closed", "resolved")
    loc = p.location if p else None
    return {
        "id": case.message.id, "status": case.status.value, "received": case.message.time.isoformat(),
        "now": now.isoformat(),
        "read": p is not None and case.status.value != "processing",
        "steps": steps,
        "decision": decision, "decision_el": DECISION_EL.get(decision or "", decision),
        "decision_en": DECISION_EN.get(decision or "", decision),
        "where": loc.label if loc and loc.label else None,
        "decided_by_person": decided,
        "questions": questions(case, decision),
        "answers": [{"text": r["text"], "at": r["at"]} for r in case.replies if r.get("by") == SENDER],
        "can_answer": case.status.value not in ("merged", "processing"),
        "second": None if not getattr(case, "second", None) or "error" in case.second else {
            k: case.second.get(k) for k in ("choice_el", "confidence", "verdict", "top")},
        "flagged": bool(p and "injection" in p.outcome.flags),
        "from_centre": [{"text": x["text"], "at": x["at"]} for x in case.to_sender],
        "advice_el": ADVICE[decision][0] if decided and decision in ADVICE else None,
        "advice_en": ADVICE[decision][1] if decided and decision in ADVICE else None,
    }


# What a person's decision means for the sender, in their words. Shown only after a person decided.
ADVICE: dict[str, tuple[str, str]] = {
    "DISPATCH_UNIT_CANDIDATE": ("Στέλνουμε βοήθεια. Μείνετε εκεί που είστε, αν είναι ασφαλές.",
                                "Help is on its way. Stay where you are if it is safe."),
    "EVACUATE_ROUTE_CANDIDATE": ("Φύγετε τώρα από την περιοχή. Για τον δρόμο, ακολουθήστε το μήνυμα του 112.",
                                 "Leave the area now. For the road, follow the 112 message."),
    "SHELTER_CANDIDATE": ("Μην βγείτε με αυτοκίνητο. Μείνετε σε κτίριο από τσιμέντο, "
                          "κλείστε πόρτες και παράθυρα.",
                          "Do not leave by car. Stay in a concrete building, close doors and windows."),
    "SEA_RESCUE_CANDIDATE": ("Ενημερώθηκε το Λιμενικό. Μείνετε μαζί, στο νερό ή στην ακτή, "
                             "μακριά από τη φωτιά.",
                             "The Coast Guard has been told. Stay together, in the water or on the shore, "
                             "away from the fire."),
    "MISSING_PERSON": ("Το κέντρο κατέγραψε τον άνθρωπο που ψάχνετε.",
                       "The centre has logged the person you are looking for."),
    "REPORTED_SAFE": ("Ευχαριστούμε. Καταγράφηκε ότι είστε ασφαλείς.", "Thank you. It is logged that you are safe."),
    "INFO": ("Ευχαριστούμε για την πληροφορία.", "Thank you for the information."),
    "MONITOR": ("Ευχαριστούμε. Το κέντρο παρακολουθεί την περιοχή σας.",
                "Thank you. The centre is watching your area."),
    "OUT_OF_AREA": ("Η περιοχή σας δεν απειλείται από αυτή τη φωτιά. Σε κίνδυνο καλέστε 112.",
                    "Your area is not threatened by this fire. In danger, call 112."),
}


SENDER = "αποστολέας (κινητό)"


def questions(case: Any, decision: str | None) -> list[str]:
    """What the desk wants to know from the sender, while it waits for an answer."""
    cb = case.callback
    if cb is not None and not case.callback_cancelled and case.callback_reply is None:
        return [q.text for q in cb.questions]
    if decision != "CALLBACK" or case.callback_reply is not None:
        return []
    p = case.processed
    note = (getattr(case, "note", None) or "").strip()
    if case.final_decision == "CALLBACK" and note.endswith(("?", ";")):
        return [note]                                   # the operator chose to call back and wrote the question
    if p and p.outcome.question_el:
        return [p.outcome.question_el]
    return [GENERIC]


GENERIC = "Πού ακριβώς βρίσκεστε (οδός και περιοχή) και πόσα άτομα είστε;"


ROLE_AGENT = {"intake": "intake", "critic": "critic", "revision": "critic", "location": "location",
              "hazard_critic": "hazard", "credibility": "credibility", "needs": "needs"}


def progress(case: Any) -> list[dict[str, Any]]:
    out = []
    for r in getattr(case, "progress", []):
        a = ROLE_AGENT.get(r["role"])
        if a is None:
            continue
        failed = r["status"] not in ("ok", "retry_ok", "cached")
        out.append({"agent": a, "el": NODE_EL.get(a, a), "en": NODE_EN.get(a, a),
                    "status": "failed" if failed else "ok", "seconds": r["seconds"], "tools": 0, "note": None})
    return out
