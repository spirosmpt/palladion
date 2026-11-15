"""Red team loop: declare first, run the desk, judge by code, propose regressions, a person approves.

  runtime/redteam/declarations.jsonl   every declaration, stored before the desk sees the message
  runtime/redteam/results.jsonl        the verdict of code for each
  eval/redteam/proposed.jsonl          failures, waiting for a person: approve (a regression test) or reject
  eval/redteam/regression.jsonl        approved failures; replayed after every change; "open" until they pass
The desk runs the messages on its own: no case service, no registry, no unit, nothing reaches the real desk.
"""
import hashlib
import json
import random
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from palladion.redteam import attacker as A
from palladion.redteam.policy import CATEGORIES, judge

LOCK = threading.RLock()
# declarations -> {id: case}. A case {"skipped": reason} was not run properly (the day's model budget ran out
# while the desk read it): it is neither a pass nor a failure, and `judge_pending` runs it again later.
Runner = Callable[[list[dict[str, Any]]], dict[str, dict[str, Any]]]


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def key(message: str) -> str:
    return hashlib.sha256(" ".join(message.split()).lower().encode()).hexdigest()[:16]


class Store:
    def __init__(self, eval_dir: Path, runtime_dir: Path):
        self.eval_dir, self.runtime_dir = Path(eval_dir), Path(runtime_dir)

    def path(self, name: str) -> Path:
        base = self.runtime_dir if name in ("declarations", "results") else self.eval_dir
        return base / f"{name}.jsonl"

    def read(self, name: str) -> list[dict[str, Any]]:
        p = self.path(name)
        if not p.exists():
            return []
        return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]

    def append(self, name: str, rec: dict[str, Any]) -> None:
        with LOCK:
            p = self.path(name)
            p.parent.mkdir(parents=True, exist_ok=True)
            with p.open("a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")

    def rewrite(self, name: str, rows: list[dict[str, Any]]) -> None:
        with LOCK:
            p = self.path(name)
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_suffix(".tmp")
            lines = "".join(json.dumps(r, ensure_ascii=False, default=str) + "\n" for r in rows)
            tmp.write_text(lines, encoding="utf-8")
            tmp.replace(p)


def judge_all(decls: list[dict[str, Any]], runner: Runner) -> dict[str, dict[str, Any]]:
    verdicts: dict[str, dict[str, Any]] = {}
    ok = []
    for d in decls:
        if A.decl_hash(d) != d.get("hash"):
            verdicts[d["id"]] = {"passed": False, "reasons": ["declaration_modified: hash mismatch"], "decision": None,
                                 "unsafe": False, "checked": []}
        else:
            ok.append(d)
    cases = runner(ok) if ok else {}
    for d in ok:
        c = cases.get(d["id"])
        if c and c.get("skipped"):
            verdicts[d["id"]] = {"passed": None, "skipped": c["skipped"], "reasons": [f"not judged: {c['skipped']}"],
                                 "decision": None, "unsafe": False, "checked": []}
            continue
        verdicts[d["id"]] = judge(c, d) if c else {"passed": False, "reasons": ["no_result"], "decision": None,
                                                   "unsafe": False, "checked": []}
        if c:
            verdicts[d["id"]]["case"] = c
    return verdicts


def run_batch(n: int, store: Store, llm: Any, gazetteer: Any, runner: Runner, seed: int = 1,
              order: list[str] | None = None) -> dict[str, Any]:
    run_id = "run-" + hashlib.sha256(f"{now()}{seed}{n}".encode()).hexdigest()[:10]
    decls = A.build(n, llm, random.Random(seed), gazetteer, order)
    for d in decls:                                  # declared and stored before the desk reads anything
        store.append("declarations", {**d, "run_id": run_id, "declared_at": now()})
    return {"run_id": run_id, **_record(decls, judge_all(decls, runner), store, run_id)}


def judge_pending(store: Store, runner: Runner) -> dict[str, Any]:
    """Declarations stored but never judged (the budget ran out): run the desk on them now. No new attacker calls."""
    # A result whose desk failed on the budget ("daily LLM budget reached") was never a real verdict either.
    done = {r["id"] for r in store.read("results") if "budget" not in str((r.get("case") or {}).get("error") or "")}
    todo: dict[str, dict[str, Any]] = {}
    for d in store.read("declarations"):
        if d["id"] not in done:
            todo.setdefault(d["id"], d)
    decls = list(todo.values())
    run_id = "pending-" + hashlib.sha256(f"{now()}{len(decls)}".encode()).hexdigest()[:8]
    return {"run_id": run_id, **_record(decls, judge_all(decls, runner), store, run_id)}


def _record(decls: list[dict[str, Any]], verdicts: dict[str, dict[str, Any]], store: Store,
            run_id: str) -> dict[str, Any]:
    known = {p["key"] for p in store.read("proposed")} | {r["key"] for r in store.read("regression")}
    results, skipped = [], []
    for d in decls:
        v = verdicts[d["id"]]
        if v.get("skipped"):
            skipped.append({"id": d["id"], "category": d["category"], "message": d["message"], "why": v["skipped"]})
            continue
        rec = {"run_id": d.get("run_id", run_id), "id": d["id"], "category": d["category"], "message": d["message"],
               "source": d["source"], "fallback_reason": d.get("fallback_reason"), "at": now(), **v}
        store.append("results", rec)
        results.append(rec)
        k = key(d["message"])
        if not v["passed"] and k not in known:
            known.add(k)
            store.append("proposed", {"proposal_id": "pr-" + k[:10], "key": k, "status": "pending",
                                      "category": d["category"], "message": d["message"], "places": d["places"],
                                      "caller_place": d.get("caller_place"), "reasons": v["reasons"],
                                      "decision": v["decision"], "unsafe": v["unsafe"]})
    _withdraw(store, {key(r["message"]) for r in results if r["passed"]})
    summary = summarize(results)
    summary["skipped"] = len(skipped)
    return {"results": results, "skipped": skipped, "summary": summary}


def _withdraw(store: Store, passed: set[str]) -> None:
    """A pending proposal whose message now passes (it was judged on a half-run desk) is withdrawn, not asked."""
    with LOCK:
        rows = store.read("proposed")
        hit = [r for r in rows if r["status"] == "pending" and r["key"] in passed]
        for r in hit:
            r["status"] = "withdrawn"
        if hit:
            store.rewrite("proposed", rows)


def summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    cats = {c: {"runs": 0, "passed": 0} for c in CATEGORIES}
    for r in results:
        cats[r["category"]]["runs"] += 1
        cats[r["category"]]["passed"] += bool(r["passed"])
    total, passed = len(results), sum(bool(r["passed"]) for r in results)
    return {"total": total, "passed": passed, "unsafe": sum(bool(r.get("unsafe")) for r in results),
            "agent_written": sum(r.get("source") == "agent" for r in results), "categories": cats}


def decide(store: Store, proposal_id: str, decision: str, actor: str) -> dict[str, Any]:
    """A person approves (it becomes a regression test, open until it passes) or rejects (never proposed again)."""
    if decision not in ("approve", "reject") or not actor.strip():
        raise ValueError("decision must be approve or reject, and say who decides")
    with LOCK:
        rows = store.read("proposed")
        row = next((r for r in rows if r["proposal_id"] == proposal_id), None)
        if row is None:
            raise KeyError(proposal_id)
        if row["status"] != "pending":
            raise ValueError("already decided")
        row.update(status="approved" if decision == "approve" else "rejected", decided_by=actor.strip())
        if decision == "approve":
            store.append("regression", {"id": "rg-" + row["key"][:10], "key": row["key"], "category": row["category"],
                                        "message": row["message"], "places": row["places"],
                                        "caller_place": row.get("caller_place"), "open": True,
                                        "reasons_at_approval": row["reasons"], "approved_by": actor.strip()})
        store.rewrite("proposed", rows)
    return row


def as_declaration(e: dict[str, Any]) -> dict[str, Any]:
    d = {"category": e["category"], "message": e["message"], "places": e.get("places", []),
         "caller_place": e.get("caller_place"), "source": "regression"}
    d["hash"] = A.decl_hash(d)
    d["id"] = e["id"]
    return d


def replay(store: Store, runner: Runner, update: bool = True) -> list[dict[str, Any]]:
    """Run every approved regression again. An open one that now passes is marked fixed; a fixed one that fails
    again is a regression."""
    entries = store.read("regression")
    decls = [as_declaration(e) for e in entries]
    verdicts = judge_all(decls, runner) if decls else {}
    out = []
    for e, d in zip(entries, decls, strict=True):
        v = verdicts[d["id"]]
        was_open = bool(e.get("open"))
        out.append({"id": e["id"], "category": e["category"], "was_open": was_open, "passed": v["passed"],
                    "fixed": was_open and v["passed"], "regressed": not was_open and not v["passed"],
                    "reasons": v["reasons"], "decision": v["decision"]})
        if update and was_open and v["passed"]:
            e["open"] = False
    if update and any(o["fixed"] for o in out):
        store.rewrite("regression", entries)
    return out
