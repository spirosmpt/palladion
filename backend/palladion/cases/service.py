"""Cases: messages arrive on the scenario clock, are triaged in the background, and wait for a person.

Flow per message
  arrives      when scenario time passes the message time
  processing   the intake agent runs in a worker thread (cached answers return at once)
  proposed     a candidate decision with reasons, waiting for an operator
  approved / changed / closed   human actions, recorded in the event registry
  resolved     a field report says everyone is out (code, from the crew's quoted words; a person can reopen)
A field report that says people remain, nobody was found, or something new is dangerous puts the case
back in the queue with a new proposed decision; any new unit is again a person's decision.

The queue is a deadline queue: open cases are ordered by how many minutes remain before the fire
front reaches them, recomputed on every read from the current scenario time. A case nobody has
acted on turns urgent when the front is 10 minutes away and overdue once it has arrived.
"""
import queue
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any

from palladion.engines.registry.registry import Actor, ActorKind, Registry
from palladion.pipeline.decide import Decision
from palladion.pipeline.process import Message, Processed

URGENT_MIN = 10.0
NO_NEWS_MIN = 20.0     # people who need help and nothing new from them or about them for this long: ask again
MOVES_PEOPLE = {Decision.EVACUATE_ROUTE_CANDIDATE.value, Decision.DISPATCH_UNIT_CANDIDATE.value,
                Decision.SHELTER_CANDIDATE.value, Decision.SEA_RESCUE_CANDIDATE.value}
STILL_THERE_EL = "Είστε ακόμα στο ίδιο σημείο; Πόσοι είστε τώρα; Έχει αλλάξει κάτι γύρω σας (φωτιά, καπνός, δρόμος);"
# Decisions that move people or send someone carry a deadline; "safe", "information" and the like do not.
ACTING = {Decision.EVACUATE_ROUTE_CANDIDATE.value, Decision.DISPATCH_UNIT_CANDIDATE.value,
          Decision.SHELTER_CANDIDATE.value, Decision.SEA_RESCUE_CANDIDATE.value, Decision.CALLBACK.value,
          Decision.REVIEW_REQUIRED.value}


class Status(StrEnum):
    processing = "processing"
    proposed = "proposed"
    approved = "approved"
    changed = "changed"
    closed = "closed"
    failed = "failed"
    merged = "merged"          # folded into an earlier case about the same people
    resolved = "resolved"      # a field report says everyone is out


OPEN = {Status.processing, Status.proposed, Status.failed}
SYSTEM = Actor(kind=ActorKind.system, id="queue")
AGENT = Actor(kind=ActorKind.agent, id="triage")
FIELD = Actor(kind=ActorKind.system, id="field-report")


@dataclass
class Case:
    message: Message
    status: Status = Status.processing
    processed: Processed | None = None
    final_decision: str | None = None
    operator: str | None = None
    note: str | None = None
    acted_at: datetime | None = None
    fire_arrival: datetime | None = None          # earliest over this case and the messages merged into it
    own_arrival: datetime | None = None           # this message's own location
    own_medical: datetime | None = None           # this message's medical clock from its needs (burns: + 15')
    medical_due: datetime | None = None           # earliest over this case and the messages merged into it
    history: list[dict[str, Any]] = field(default_factory=list)
    duplicate: Any = None                         # DuplicateResult suggested by the agent, if any
    duplicate_dismissed: bool = False
    merged_into: str | None = None
    linked: list[str] = field(default_factory=list)
    reopened_by: str | None = None                # a merged message reopened this handled case
    callback: Any = None                          # CallbackPlan sent to the sender
    callback_sent: datetime | None = None
    callback_due: datetime | None = None          # when the (simulated) reply arrives
    callback_reply: str | None = None
    callback_cancelled: bool = False
    before_callback: dict[str, Any] | None = None  # the triage before the reply, for the operator to compare
    field_reports: list[dict[str, Any]] = field(default_factory=list)   # field outcomes applied, oldest first
    field_people: int | None = None               # people still there, after a field report
    replies: list[dict[str, Any]] = field(default_factory=list)   # what the sender answered, oldest first
    second: dict[str, Any] | None = None          # Oumi Iris's second opinion on the latest reading
    to_sender: list[dict[str, Any]] = field(default_factory=list)   # what a person wrote back to the public page
    progress: list[dict[str, Any]] = field(default_factory=list)    # model calls of the reading under way, as they end


class CaseError(Exception):
    pass


class StaleCase(CaseError):
    """The case changed after the operator looked at it."""


class CaseService:
    def __init__(self, messages: list[Message], triage, registry: Registry, dedupe=None, callback=None,
                 simulator=None, contra_ask=None, second=None, watch=None):
        """`triage(msg) -> Processed` runs the agents; it may be slow (LLM) and is called off-thread.
        `dedupe(processed, earlier_processed) -> DuplicateResult | None` suggests a merge (optional)."""
        self.messages = sorted(messages, key=lambda m: m.time)
        self._triage = triage
        self._dedupe = dedupe
        self._callback = callback            # plan(processed, channel) -> CallbackPlan | None
        self._simulator = simulator          # Simulator with hidden replies (synthetic messages only)
        self._contra_ask = contra_ask        # ask(item, message_text, language) -> ContraQuestion (optional agent)
        self._second = second                # opinion(text, decision) -> dict: Oumi Iris, never decides (optional)
        self._watch = watch                  # watch(fn): context in which every model call is told to fn as it ends
        self._asking: set[str] = set()
        self.registry = registry
        self._lock = threading.RLock()
        self._cases: dict[str, Case] = {}
        self._queue: queue.Queue[tuple[Message, bool]] = queue.Queue()
        self._worker: threading.Thread | None = None
        self._stop = threading.Event()
        # Messages an operator typed in earlier (a call, the radio) come back after a restart.
        for ev in registry.events():
            if ev.type == "case.submitted":
                self.messages.append(_submitted(ev.stream.removeprefix("case:"), ev.data, ev.actor.id))
        self.messages.sort(key=lambda m: m.time)

    # -- messages an operator enters --------------------------------------------------------------
    CHANNELS = ("199_call", "166_call", "100_call", "112_sms", "radio", "field", "social")

    def submit(self, text: str, channel: str, operator: str, now: datetime, received_at: datetime | None = None,
               point: tuple[float, float] | None = None, sync: bool = False) -> Message:
        """A call or a radio message the operator typed in. It goes through the same agents as any message;
        the operator is recorded as who entered it, so the text is theirs, not a sender's."""
        from palladion.textclean import clean
        text = clean(text).strip()
        if len(text) < 3:
            raise CaseError("write the message")
        if channel not in self.CHANNELS:
            raise CaseError(f"unknown channel {channel}")
        at = received_at or now
        if at > now:
            raise CaseError("the time received is later than the scenario clock")
        return self._add(text, channel, operator, "op", now, at, point, sync)

    def submit_public(self, text: str, now: datetime) -> Message:
        """A message someone wrote on the live page (palladion/live.py): the sender's own words, at the replay's
        clock. Same agents, same queue, same person deciding."""
        from palladion.textclean import clean
        text = clean(text).strip()
        if len(text) < 3:
            raise CaseError("write the message")
        return self._add(text, "live_web", "σελίδα πολιτών", "w", now, now, None, False, public=True)

    def _add(self, text: str, channel: str, actor: str, prefix: str, now: datetime, at: datetime,
             point: tuple[float, float] | None, sync: bool, public: bool = False) -> Message:
        with self._lock:
            used = {m.id for m in self.messages}
            n = 1 + sum(1 for i in used if i.startswith(prefix))
            while f"{prefix}{n:02d}" in used:
                n += 1
            cid = f"{prefix}{n:02d}"
            data: dict[str, Any] = {"text": text, "channel": channel, "received_at": at.isoformat(),
                                    "point": list(point) if point else None}
            if public:
                data["public"] = True
            self.registry.append(f"case:{cid}", "case.submitted", Actor(kind=ActorKind.human, id=actor), data,
                                 expected_version=0, sim_time=now)
            m = _submitted(cid, data, actor)
            self.messages = sorted([*self.messages, m], key=lambda x: x.time)   # a new list: tick may be iterating
        self.tick(now, sync=sync)
        return m

    # -- arrivals and triage ------------------------------------------------------------------------
    def tick(self, now: datetime, sync: bool = False) -> None:
        """Let every message up to `now` arrive. With sync=True triage runs inline (tests, CLI)."""
        for m in self.messages:
            if m.time > now:
                break
            with self._lock:
                if m.id in self._cases:
                    continue
                self._cases[m.id] = Case(m)
            self._append(m.id, "case.received", SYSTEM, {"channel": m.channel, "text": m.text}, m.time)
            if sync:
                self._run(m)
            else:
                self._queue.put((m, False))
        # Replies to call-backs that are due: the case is read again with the answer.
        due = []
        with self._lock:
            for c in self._cases.values():
                if (c.callback_due is not None and c.callback_due <= now and c.callback_reply is None
                        and not c.callback_cancelled and self._simulator is not None):
                    r = self._simulator.reply(c.message.id)
                    if r is not None:
                        c.callback_reply = r.text
                        due.append((c, r))
        for c, r in due:
            from palladion.cases.simulator import PREFIX
            self._append(c.message.id, "callback.reply", SYSTEM, {"text": PREFIX + r.text, "simulated": True},
                         c.callback_due or now)
            with self._lock:
                c.replies.append({"text": r.text, "by": None, "at": (c.callback_due or now).isoformat(),
                                  "simulated": True})
            m = self._with_replies(c, c.callback_due or now)
            if sync:
                self._run(m, retriage=True)
            else:
                self._queue.put((m, True))
        if sync:                                   # tests and the CLI: whatever was queued runs now
            while not self._queue.empty():
                m, again = self._queue.get_nowait()
                self._run(m, retriage=again)

    def start(self, clock_now) -> None:
        """Background: a ticker that follows the clock and one worker (respects the LLM rate limit)."""
        def ticker() -> None:
            while not self._stop.wait(0.5):
                self.tick(clock_now())

        def worker() -> None:
            while not self._stop.is_set():
                try:
                    m, again = self._queue.get(timeout=0.5)
                except queue.Empty:
                    continue
                self._run(m, retriage=again)

        threading.Thread(target=ticker, daemon=True, name="case-ticker").start()
        self._worker = threading.Thread(target=worker, daemon=True, name="case-worker")
        self._worker.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self, m: Message, retriage: bool = False) -> None:
        with self._lock:
            c0 = self._cases.get(m.id)
            if c0 is not None:
                c0.progress = []

        def seen(rec) -> None:  # noqa: ANN001 - a CallRecord, as each agent finishes
            if c0 is not None:
                secs = rec.original_seconds if rec.status == "cached" and rec.original_seconds else rec.seconds
                with self._lock:
                    c0.progress.append({"role": rec.role, "status": rec.status, "seconds": round(secs, 2)})

        try:
            if self._watch is not None:
                with self._watch(seen):
                    p = self._triage(m)
            else:
                p = self._triage(m)
        except Exception as exc:  # noqa: BLE001 - a failed case must still reach a person
            with self._lock:
                c = self._cases[m.id]
                c.status, c.history = Status.failed, c.history + [{"error": str(exc)}]
            return
        suggestion = None
        if self._dedupe is not None and p.error is None:
            with self._lock:
                earlier = [x.processed for x in self._cases.values()
                           if x.processed is not None and x.message.time <= m.time and x.message.id != m.id]
            try:
                suggestion = self._dedupe(p, earlier)
            except Exception as exc:  # noqa: BLE001 - a failed duplicate check must not lose the case
                suggestion = None
                p.outcome.flags.append("duplicate_check_failed")
                p.outcome.reasons.append(f"Ο έλεγχος διπλοεγγραφής απέτυχε: {exc}")
        with self._lock:
            c = self._cases[m.id]
            if retriage and c.processed is not None:
                old = c.processed
                c.before_callback = {"decision": old.outcome.decision.value, "location": old.location.label,
                                     "location_status": old.location.status,
                                     "people": (old.intake.output.people.count
                                                if old.intake and old.intake.output.people else None)}
                if c.status not in OPEN:
                    c.status, c.reopened_by = Status.proposed, f"{m.id} (απάντηση)"
            c.processed = p
            if p.minutes_to_front is not None:
                c.own_arrival = m.time + timedelta(minutes=p.minutes_to_front)
            clock = (p.needs or {}).get("clock_min")
            c.own_medical = c.message.time + timedelta(minutes=clock) if clock else None
            c.duplicate = suggestion if suggestion is not None and suggestion.match_id else None
            self._refresh_deadline(c)
            if c.status == Status.processing:
                c.status = Status.failed if p.error else Status.proposed
        self._append(m.id, "case.retriaged" if retriage else "case.triaged", AGENT, {
            "decision": p.outcome.decision.value, "priority": p.outcome.priority,
            "location": {"status": p.location.status, "lon": p.location.lon, "lat": p.location.lat,
                         "label": p.location.label},
            "error": p.error, "retriage": retriage,
            "duplicate_of": c.duplicate.match_id if c.duplicate else None}, m.time)
        self._second_opinion(c, m, p)
        if not retriage:
            answered = self._restore_human_state(m.id)
            self._maybe_call_back(c, p)
            if answered:                     # after a restart: read it again with what the sender said
                self._queue.put((self._with_replies(c, c.message.time), True))

    def _second_opinion(self, c: Case, m: Message, p) -> None:
        """Iris reads the same words. Code compares; the operator sees a flag when they differ. Nothing else moves."""
        if self._second is None or p.error:
            return
        try:
            o = self._second(m.text, p.outcome.decision.value)
        except Exception as exc:  # noqa: BLE001 - a second opinion that fails must not touch the case
            o = {"error": str(exc)[:200]}
        with self._lock:
            c.second = o
        self._append(m.id, "case.second_opinion", AGENT, {k: o.get(k) for k in ("choice", "confidence", "verdict",
                                                                               "urgent", "genuine", "error")}, m.time)

    def _maybe_call_back(self, c: Case, p) -> None:
        """Questions go out at once when the decision is a call-back (a system action, logged)."""
        if self._callback is None or p.error or p.outcome.decision.value != "CALLBACK":
            return
        plan = self._callback(p, c.message.channel)
        if plan is None:
            return
        sent = c.message.time + timedelta(minutes=1)
        with self._lock:
            c.callback, c.callback_sent = plan, sent
            r = self._simulator.reply(c.message.id) if self._simulator is not None else None
            c.callback_due = sent + timedelta(minutes=r.delay_min) if r is not None else None
        self._append(c.message.id, "callback.sent", AGENT,
                     {"items": plan.items, "questions": [q.text for q in plan.questions], "source": plan.source},
                     sent)

    def _with_replies(self, c: Case, t: datetime) -> Message:
        """The message read again: the original words and every answer since, each marked as such."""
        text = c.message.text + "".join(f"\n\n[Απάντηση στην επανάκληση] {r['text']}" for r in c.replies)
        return c.message.model_copy(update={"text": text, "time": t})

    # -- human actions --------------------------------------------------------------------------------
    def answer(self, case_id: str, text: str, operator: str, now: datetime, sync: bool = False) -> Case:
        """What the sender said when the operator called back (or texted, or reached them on the radio).
        The case is read again with the original message and every answer; a handled case comes back to the
        queue if the reading changes. The earlier answers stay: nothing the sender said is replaced."""
        from palladion.textclean import clean
        text = clean(text).strip()
        if len(text) < 2:
            raise CaseError("write what the sender answered")
        with self._lock:
            c = self._cases.get(case_id)
            if c is None:
                raise CaseError("no such case")
            if c.status == Status.merged:
                raise CaseError(f"the case was merged into {c.merged_into}: enter the answer there")
            if c.status == Status.processing:
                raise CaseError("the case is still being read; try again in a moment")
            c.replies.append({"text": text, "by": operator or "operator", "at": now.isoformat(), "simulated": False})
            c.callback_reply, c.callback_due = text, None        # a real answer: no simulated one after it
        human = Actor(kind=ActorKind.human, id=operator or "operator")
        self.registry.append(f"case:{case_id}", "callback.answered", human, {"text": text},
                             expected_version=self.registry.version(f"case:{case_id}"), sim_time=now)
        m = self._with_replies(c, now)
        if sync:
            self._run(m, retriage=True)
        else:
            self._queue.put((m, True))
        return c

    def tell_sender(self, case_id: str, text: str, operator: str, now: datetime) -> Case:
        """A person writes back to someone who wrote from the public page. Only a person: never an agent."""
        from palladion.textclean import clean
        text = clean(text).strip()
        if len(text) < 2:
            raise CaseError("write the message")
        with self._lock:
            c = self._cases.get(case_id)
            if c is None:
                raise CaseError("no such case")
            if c.message.channel != "live_web":
                raise CaseError("only messages from the public page can be answered here")
            c.to_sender.append({"text": text, "by": operator or "operator", "at": now.isoformat()})
        human = Actor(kind=ActorKind.human, id=operator or "operator")
        # its own stream: writing to the sender does not change the case the operator is looking at
        self.registry.append(f"sender:{case_id}", "case.sender_told", human, {"text": text},
                             expected_version=self.registry.version(f"sender:{case_id}"), sim_time=now)
        return c

    def cancel_callback(self, case_id: str, operator: str, now: datetime) -> Case:
        with self._lock:
            c = self._cases.get(case_id)
            if c is None or c.callback is None:
                raise CaseError("no call-back to stop")
            if c.callback_reply is not None:
                raise CaseError("the reply has already arrived")
            c.callback_cancelled = True
        human = Actor(kind=ActorKind.human, id=operator or "operator")
        self.registry.append(f"case:{case_id}", "callback.cancelled", human, {},
                             expected_version=self.registry.version(f"case:{case_id}"), sim_time=now)
        return c

    def version(self, case_id: str) -> int:
        """What the operator saw: every event of the case (a new message, a reply, a field report, a decision)
        moves it on. An action sent with an older version is refused: decide on what is there now."""
        return self.registry.version(f"case:{case_id}")

    def act(self, case_id: str, action: str, operator: str, now: datetime, decision: str | None = None,
            note: str | None = None, target: str | None = None, expected_version: int | None = None) -> Case:
        with self._lock:
            c = self._cases.get(case_id)
            if c is None:
                raise CaseError(f"unknown case {case_id}")
            if expected_version is not None and expected_version != self.version(case_id):
                raise StaleCase("Το περιστατικό άλλαξε από τότε που το άνοιξες (νέο μήνυμα, απάντηση ή αναφορά). "
                                "Δες τι άλλαξε και αποφάσισε ξανά.")
            if c.status == Status.processing:
                raise CaseError("the case is still being triaged")
            if c.status == Status.merged and action != "unmerge":
                raise CaseError(f"the case is merged into {c.merged_into}; act on that case or unmerge first")
            proposed = c.processed.outcome.decision.value if c.processed else None
            data: dict[str, Any]
            final: str | None
            if action == "approve":
                if proposed is None:
                    raise CaseError("nothing to approve: triage failed; choose a decision instead")
                etype, data, status, final = "case.approved", {"decision": proposed}, Status.approved, proposed
            elif action == "change":
                if decision not in {d.value for d in Decision}:
                    raise CaseError(f"unknown decision {decision}")
                etype, status, final = "case.decision_changed", Status.changed, decision
                data = {"from": proposed, "to": decision}
            elif action == "close":
                etype, data, status, final = "case.closed", {}, Status.closed, c.final_decision
            elif action == "reopen":
                etype, data, status, final = "case.reopened", {}, Status.proposed, None
            elif action == "cancel_callback":
                return self.cancel_callback(case_id, operator, now)
            elif action in ("merge", "not_duplicate", "unmerge"):
                return self._dup_action(c, action, operator, now, target, note)
            else:
                raise CaseError(f"unknown action {action}")
            if note:
                data["note"] = note
            human = Actor(kind=ActorKind.human, id=operator or "operator")
            self.registry.append(f"case:{case_id}", etype, human, data,
                                 expected_version=self.registry.version(f"case:{case_id}"), sim_time=now)
            c.status, c.final_decision, c.operator, c.note, c.acted_at = status, final, human.id, note, now
            c.reopened_by = None
            return c

    def _dup_action(self, c: Case, action: str, operator: str, now: datetime, target: str | None,
                    note: str | None) -> Case:
        human = Actor(kind=ActorKind.human, id=operator or "operator")

        def rec(case_id: str, etype: str, data: dict[str, Any]) -> None:
            if note:
                data = {**data, "note": note}
            self.registry.append(f"case:{case_id}", etype, human, data,
                                 expected_version=self.registry.version(f"case:{case_id}"), sim_time=now)

        if action == "merge":
            into = target or (c.duplicate.match_id if c.duplicate else None)
            primary = self._cases.get(into or "")
            if primary is None or primary.message.id == c.message.id:
                raise CaseError("nothing to merge into")
            while primary.merged_into:                    # always merge into the root case
                primary = self._cases[primary.merged_into]
            reopens = primary.status not in OPEN
            rec(c.message.id, "case.merged", {"into": primary.message.id})
            rec(primary.message.id, "case.linked", {"from": c.message.id, "reopened": reopens})
            self._apply_merge(c, primary, reopen=reopens)
        elif action == "unmerge":
            if not c.merged_into:
                raise CaseError("the case is not merged")
            primary = self._cases[c.merged_into]
            rec(c.message.id, "case.unmerged", {"from": primary.message.id})
            rec(primary.message.id, "case.unlinked", {"from": c.message.id})
            self._apply_unmerge(c, primary)
        else:  # not_duplicate
            if c.duplicate is None:
                raise CaseError("no duplicate suggestion to dismiss")
            rec(c.message.id, "case.not_duplicate", {"suggested": c.duplicate.match_id})
            c.duplicate_dismissed = True
        c.operator, c.acted_at = human.id, now
        return c

    def _apply_merge(self, c: Case, primary: Case, reopen: bool = False) -> None:
        c.status, c.merged_into = Status.merged, primary.message.id
        if c.message.id not in primary.linked:
            primary.linked.append(c.message.id)
        if reopen:
            primary.status = Status.proposed     # new information reopens a handled case
            primary.reopened_by = c.message.id
        self._refresh_deadline(primary)

    def _apply_unmerge(self, c: Case, primary: Case) -> None:
        c.status, c.merged_into = Status.proposed, None
        primary.linked = [x for x in primary.linked if x != c.message.id]
        self._refresh_deadline(primary)

    def _refresh_deadline(self, c: Case) -> None:
        raw = [c.own_arrival] + [self._cases[x].own_arrival for x in c.linked if x in self._cases]
        times: list[datetime] = [t for t in raw if t is not None]
        c.fire_arrival = min(times) if times else None
        medical = [x for x in [c.own_medical] + [self._cases[i].own_medical for i in c.linked if i in self._cases]
                   if x is not None]
        c.medical_due = min(medical) if medical else None

    def _restore_human_state(self, case_id: str) -> bool:
        """After a restart, decisions people made earlier come back from the registry.
        True when the sender's answers came back too, so the case must be read again with them."""
        answered_again = False
        with self._lock:
            c = self._cases[case_id]
            for ev in self.registry.events(f"sender:{case_id}"):     # what a person wrote back to the sender
                at = ev.sim_time.isoformat() if ev.sim_time else None
                if not any(x["at"] == at and x["text"] == ev.data.get("text") for x in c.to_sender):
                    c.to_sender.append({"text": ev.data.get("text", ""), "by": ev.actor.id, "at": at})
            for ev in self.registry.events(f"case:{case_id}"):
                if ev.type == "case.field_outcome":
                    if not any(x.get("report") == ev.data.get("report") for x in c.field_reports):
                        self._apply_field(c, ev.data)
                    continue
                if ev.actor.kind is not ActorKind.human or ev.type in ("case.submitted", "case.contradiction_decided"):
                    # entering a message or judging a contradiction is not acting on the case
                    continue
                if ev.type == "callback.answered":                   # what the sender said; not a decision
                    if not any(r.get("at") == (ev.sim_time.isoformat() if ev.sim_time else None)
                               and r["text"] == ev.data.get("text") for r in c.replies):
                        c.replies.append({"text": ev.data.get("text", ""), "by": ev.actor.id,
                                          "at": ev.sim_time.isoformat() if ev.sim_time else None, "simulated": False})
                        c.callback_reply, c.callback_due = ev.data.get("text"), None
                        answered_again = True
                    continue
                c.operator, c.acted_at, c.note = ev.actor.id, ev.sim_time, ev.data.get("note")
                if ev.type == "case.approved":
                    c.status, c.final_decision = Status.approved, ev.data.get("decision")
                elif ev.type == "case.decision_changed":
                    c.status, c.final_decision = Status.changed, ev.data.get("to")
                elif ev.type == "case.closed":
                    c.status = Status.closed
                elif ev.type == "case.reopened":
                    c.status, c.final_decision = Status.proposed, None
                elif ev.type == "callback.cancelled":
                    c.callback_cancelled = True
                elif ev.type == "case.not_duplicate":
                    c.duplicate_dismissed = True
                elif ev.type == "case.merged" and ev.data.get("into") in self._cases:
                    self._apply_merge(c, self._cases[ev.data["into"]])   # the primary's own log replays reopening
                elif ev.type == "case.linked":
                    if ev.data.get("from") not in c.linked:
                        c.linked.append(ev.data["from"])
                    if ev.data.get("reopened"):
                        c.status, c.final_decision, c.reopened_by = Status.proposed, None, ev.data["from"]
                elif ev.type == "case.unlinked":
                    c.linked = [x for x in c.linked if x != ev.data.get("from")]
                elif ev.type == "case.unmerged" and c.merged_into in self._cases:
                    self._apply_unmerge(c, self._cases[c.merged_into])
        return answered_again

    def _append(self, case_id: str, etype: str, actor: Actor, data: dict[str, Any], t: datetime) -> None:
        """System and agent events are written once per case, so a restart does not duplicate them."""
        stream = f"case:{case_id}"
        if any(ev.type == etype for ev in self.registry.events(stream)):
            return
        self.registry.append(stream, etype, actor, data, expected_version=self.registry.version(stream), sim_time=t)

    # -- field reports (code applies them; nothing here sends anyone) ---------------------------------
    def people(self, case_id: str) -> int | None:
        c = self._cases.get(case_id)
        if c is None:
            return None
        if c.field_people is not None:
            return c.field_people
        o = c.processed.intake.output if c.processed and c.processed.intake else None
        return o.people.count if o and o.people else None

    def apply_field(self, case_id: str, report: Any) -> None:
        o = report.outcome
        if o is None or o.status is None:
            return
        data = {"report": report.id, "unit": report.unit, "mission": report.mission, "text": report.text,
                "simulated": report.simulated, "at": report.at.isoformat(), "outcome": o.to_json()}
        stream = f"case:{case_id}"
        with self._lock:
            c = self._cases.get(case_id)
            if c is None or any(x.get("report") == report.id for x in c.field_reports):
                return
            self.registry.append(stream, "case.field_outcome", FIELD, data,
                                 expected_version=self.registry.version(stream), sim_time=report.at)
            self._apply_field(c, data)

    def _apply_field(self, c: Case, data: dict[str, Any]) -> None:
        o = data["outcome"]
        c.field_reports.append(data)
        if o.get("remaining") is not None:
            c.field_people = o["remaining"]
        if o["status"] == "resolved":
            c.status, c.reopened_by = Status.resolved, None
            return
        if o.get("decision") and c.processed is not None:
            old = c.processed.outcome.decision.value
            c.processed.outcome.decision = Decision(o["decision"])
            c.processed.outcome.reasons.insert(0, f"Αναφορά πεδίου ({data['unit']}): {o['reason_el']}")
            if old != o["decision"]:
                c.processed.outcome.flags.append("field_changed")
        c.status, c.final_decision, c.reopened_by = Status.proposed, None, f"αναφορά πεδίου ({data['unit']})"

    # -- contradictions between sources (advisory; a person decides which side holds) -----------------
    VERDICTS = ("first", "second", "not_a_contradiction")

    def contradictions(self, case_id: str, now: datetime | None = None, ask: bool = False) -> list[dict[str, Any]]:
        """`ask`: when the operator opens the case, the question agent writes a question for each open item that
        has none yet (in the background; the fixed question stands until it is done, and if it is rejected)."""
        from palladion.cases.contradictions import detect
        c = self.get(case_id)
        if c is None:
            return []
        decided: dict[str, dict[str, Any]] = {}
        asked: dict[str, dict[str, Any]] = {}
        for ev in self.registry.events(f"case:{case_id}"):
            if ev.type == "case.contradiction_decided":
                decided[ev.data["item"]] = {"verdict": ev.data["verdict"], "by": ev.actor.id,
                                            "at": ev.sim_time.isoformat() if ev.sim_time else None,
                                            "note": ev.data.get("note")}
            elif ev.type == "case.contradiction_question":
                asked[ev.data["item"]] = ev.data
        with self._lock:
            items = detect(c, self._cases.get, decided)
        for it in items:
            it["question_fixed_el"] = it["question_el"]
            q = asked.get(it["id"])
            if q is not None:
                it["question_source"] = q["source"]
                if q["source"] == "agent":
                    it["question_el"] = q["text"]
            elif self._contra_ask is not None and it["decision"] is None and it["question_el"]:
                it["question_source"] = "pending"
                if ask:
                    self._ask_later(case_id, it, now or c.message.time)
            else:
                it["question_source"] = "fixed"
        return items

    def _ask_later(self, case_id: str, item: dict[str, Any], now: datetime) -> None:
        key = f"{case_id}|{item['id']}"
        with self._lock:
            if key in self._asking:
                return
            self._asking.add(key)
        threading.Thread(target=self.contradiction_question, args=(case_id, item, now), daemon=True).start()

    def contradiction_question(self, case_id: str, item: dict[str, Any], now: datetime) -> dict[str, Any] | None:
        """The question agent, for one item (synchronous). Logged as a registry event, kept or not."""
        c = self.get(case_id)
        if c is None or self._contra_ask is None:
            return None
        o = c.processed.intake.output if c.processed and c.processed.intake else None
        res = self._contra_ask(item, c.message.text, str(o.language) if o else "el")
        data = {"item": item["id"], "text": res.text, "source": res.source, "problems": res.problems,
                "call": res.call}
        stream = f"case:{case_id}"
        with self._lock:
            if any(ev.type == "case.contradiction_question" and ev.data.get("item") == item["id"]
                   for ev in self.registry.events(stream)):
                return data                  # written once per item, also across a restart
            self.registry.append(stream, "case.contradiction_question",
                                 Actor(kind=ActorKind.agent, id="contra_question"), data,
                                 expected_version=self.registry.version(stream), sim_time=max(now, c.message.time))
        return data

    def decide_contradiction(self, case_id: str, item: str, verdict: str, operator: str, now: datetime,
                             note: str | None = None) -> list[dict[str, Any]]:
        if verdict not in self.VERDICTS:
            raise CaseError(f"verdict must be one of {', '.join(self.VERDICTS)}")
        if not any(it["id"] == item for it in self.contradictions(case_id)):
            raise CaseError("no such contradiction on this case (it may have been resolved by newer data)")
        human = Actor(kind=ActorKind.human, id=operator or "operator")
        self.registry.append(f"case:{case_id}", "case.contradiction_decided", human,
                             {"item": item, "verdict": verdict, "note": (note or "").strip() or None},
                             expected_version=self.registry.version(f"case:{case_id}"), sim_time=now)
        return self.contradictions(case_id)

    # -- reads ----------------------------------------------------------------------------------------
    def earlier_places(self, name: str, before: datetime) -> list[dict[str, Any]]:
        """Earlier messages placed on a street or area of this name (the location agent's earlier_messages tool)."""
        from palladion.field.extract import fold
        key = fold(name).strip()
        out = []
        for c in list(self._cases.values()):
            p = c.processed
            loc = p.location if p else None
            if loc is None or loc.lon is None or loc.lat is None or c.message.time >= before or not key:
                continue
            if loc.status not in ("resolved", "approximate") or key not in fold(loc.label or ""):
                continue
            out.append({"case": c.message.id, "name": (loc.label or "").split(",")[0].strip(), "area": loc.area,
                        "status": loc.status, "lon": loc.lon, "lat": loc.lat, "extent_m": loc.uncertainty_m,
                        "minutes_before": round((before - c.message.time) / timedelta(minutes=1))})
        return sorted(out, key=lambda x: x["minutes_before"])

    def get(self, case_id: str) -> Case | None:
        with self._lock:
            return self._cases.get(case_id)

    def visible(self, now: datetime) -> list[Case]:
        with self._lock:
            cases = [c for c in self._cases.values() if c.message.time <= now]

        def key(c: Case):
            group = 0 if c.status in OPEN and has_deadline(c) else 1 if c.status in OPEN else 2
            if c.status == Status.merged:
                group = 3
            mins = deadline_left(c, now)
            prio = c.processed.outcome.priority if c.processed else 50
            return (group, mins if mins is not None else 1e6, -prio, c.message.time)
        return sorted(cases, key=key)


def _submitted(cid: str, data: dict[str, Any], operator: str) -> Message:
    pt = data.get("point")
    return Message(id=cid, time=datetime.fromisoformat(data["received_at"]), channel=data["channel"],
                   text=data["text"], point=(pt[0], pt[1]) if pt else None,
                   entered_by=None if data.get("public") else operator)   # the public writes its own words


def deadline(c: Case) -> tuple[datetime | None, str | None]:
    """The earlier of the front and the medical clock, and which one it is."""
    options = [(t, why) for t, why in ((c.fire_arrival, "front"), (c.medical_due, "medical")) if t is not None]
    return min(options, key=lambda x: x[0]) if options else (None, None)


def deadline_left(c: Case, now: datetime) -> float | None:
    t, _ = deadline(c)
    return None if t is None else (t - now) / timedelta(minutes=1)


def minutes_left(c: Case, now: datetime) -> float | None:
    """Minutes until the FRONT reaches the case (the medical clock is separate: deadline_left)."""
    if c.fire_arrival is None:
        return None
    return (c.fire_arrival - now) / timedelta(minutes=1)


def has_deadline(c: Case) -> bool:
    if c.processed is None:
        return c.status == Status.processing      # not triaged yet: assume it may be urgent
    return c.processed.outcome.decision.value in ACTING or c.reopened_by is not None


def urgency(c: Case, now: datetime) -> str:
    """normal | urgent | overdue, for open cases whose decision moves people or sends someone."""
    if c.status not in OPEN or not has_deadline(c):
        return "normal"
    m = deadline_left(c, now)
    if m is None:
        return "normal"
    if m <= 0:
        return "overdue"
    return "urgent" if m <= URGENT_MIN else "normal"


def _mins(m: float) -> str:
    return f"{round(m)}′"


def why_here(c: Case, now: datetime) -> list[dict[str, str]]:
    """Why the case sits where it does in the queue, in the order the sort uses (CaseService.visible)."""
    out: list[dict[str, str]] = []
    if c.status == Status.merged:
        return [{"el": f"Συγχωνεύτηκε στο {c.merged_into}: στο τέλος της λίστας.",
                 "en": f"Merged into {c.merged_into}: at the end of the list."}]
    if c.status not in OPEN:
        return [{"el": "Διεκπεραιώθηκε: κάτω από τα ανοιχτά.", "en": "Handled: below the open cases."}]
    if not has_deadline(c):
        out.append({"el": "Ανοιχτό, αλλά η πρόταση δεν μετακινεί ανθρώπους ούτε στέλνει κάποιον: μετά από όσα έχουν "
                          "προθεσμία.",
                    "en": "Open, but the proposal moves nobody and sends no one: after the cases with a deadline."})
    else:
        out.append({"el": "Ανοιχτό με προθεσμία: η πρόταση μετακινεί ανθρώπους ή στέλνει κάποιον.",
                    "en": "Open with a deadline: the proposal moves people or sends someone."})
    t, why = deadline(c)
    left = deadline_left(c, now)
    if t is None or left is None:
        out.append({"el": "Δεν υπάρχει εκτίμηση για το πότε φτάνει το μέτωπο εκεί.",
                    "en": "No estimate of when the front reaches it."})
    elif why == "medical":
        needs = (c.processed.needs or {}) if c.processed else {}
        what = needs.get("summary_el") or "ανάγκη υγείας"
        out.append({"el": f"Ιατρική προθεσμία ({what}, {needs.get('clock_min')}′ από το μήνυμα): "
                          + (f"μένουν {_mins(left)}." if left > 0 else f"πέρασε πριν από {_mins(-left)}."),
                    "en": f"Medical clock ({needs.get('clock_min')}′ from the message): "
                          + (f"{_mins(left)} left." if left > 0 else f"passed {_mins(-left)} ago.")})
        front = minutes_left(c, now)
        if front is not None:
            out.append({"el": f"Είναι νωρίτερα από το μέτωπο ({_mins(front)}).",
                        "en": f"It comes before the front ({_mins(front)})."})
    else:
        out.append({"el": (f"Το μέτωπο φτάνει σε {_mins(left)}." if left > 0
                           else f"Το μέτωπο έφτασε πριν από {_mins(-left)}."),
                    "en": f"The front arrives in {_mins(left)}." if left > 0
                    else f"The front arrived {_mins(-left)} ago."})
    if c.linked:
        out.append({"el": f"Η προθεσμία είναι η νωρίτερη μαζί με τα συγχωνευμένα ({', '.join(c.linked)}).",
                    "en": f"The deadline is the earliest with the merged messages ({', '.join(c.linked)})."})
    out.append({"el": "Με ίση προθεσμία: πρώτα η υψηλότερη προτεραιότητα της πρότασης, μετά το παλαιότερο μήνυμα.",
                "en": "With equal deadlines: higher proposal priority first, then the older message."})
    return out


def last_news(c: Case, get=None) -> datetime:
    """The latest thing heard from or about the people: their message, a merged one, an answer, a crew report."""
    times = [c.message.time]
    if get is not None:
        times += [x.message.time for x in (get(i) for i in c.linked) if x is not None]
    times += [datetime.fromisoformat(r["at"]) for r in c.replies if r.get("at")]
    times += [datetime.fromisoformat(f["at"]) for f in c.field_reports if f.get("at")]
    return max(times)


def no_news(c: Case, now: datetime, get=None) -> dict[str, Any] | None:
    """Still waiting for help and nothing new for NO_NEWS_MIN: propose asking "are you still there?".
    A proposal for a person to make the call, never a message sent by itself."""
    if c.status not in (Status.proposed, Status.failed, Status.approved, Status.changed):
        return None
    decision = c.final_decision or (c.processed.outcome.decision.value if c.processed else None)
    if decision not in MOVES_PEOPLE:
        return None
    quiet = (now - last_news(c, get)) / timedelta(minutes=1)
    if quiet < NO_NEWS_MIN:
        return None
    return {"minutes": round(quiet), "question_el": STILL_THERE_EL}
