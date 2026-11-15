"""Supervisor watchers: code looks at the whole desk and finds what is slipping. No model here.

Each finding carries the ids it is about (cases, units), the numbers code computed, one Greek
fact text (the only thing the supervisor agent may cite), the actions a person may take on it, and how
severe it is. Only what has happened by the scenario time is seen: no hindsight.

  front_no_decision   cases that move people, still without a decision, with the front <= 10' away or there
  approved_not_sent   "send a unit" approved, but no unit sent after 5'
  idle_units          land units free while "send a unit" cases wait >= 5'
  unit_silent         a unit has not said "we arrived" 10' after its estimated arrival
  unit_idle           a unit on scene 15' longer than planned, with no word of what it found
  unit_down           a unit reported a breakdown and its case has no other unit on the way
  callback_aging      a call-back sent 15' ago without a reply
  no_news             people waiting for help, nothing new from or about them for 20' (ask "still there?")
  cluster             >= 3 new open cases within 500 m in 20': maybe the whole area needs a 112 warning.
                      Resolved when its cases have all been decided, not when the time window moves on.
  coast_guard         a message from the sea or the shore waits 10' with no request sent to the Coast Guard
"""
import hashlib
import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

ACTION_EL = {
    "open_case": "Άνοιγμα περιστατικού",
    "acknowledge": "Ελήφθη· επικοινωνία με πλήρωμα ή αποστολέα",
    "request_alert": "Πρόταση 112 για την περιοχή",
    "open_coastguard": "Άνοιγμα αιτημάτων Λιμενικού",
}
OWNER_EL = {
    "open_case": "χειριστής περιστατικών", "acknowledge": "χειριστής ασυρμάτου", "request_alert": "υπεύθυνος 112",
    "open_coastguard": "σύνδεσμος Λιμενικού",
}
ALLOWED = {
    "front_no_decision": ("open_case", "acknowledge"),
    "approved_not_sent": ("open_case",),
    "idle_units": ("open_case",),
    "unit_silent": ("acknowledge",),
    "unit_idle": ("acknowledge",),
    "unit_down": ("open_case", "acknowledge"),
    "callback_aging": ("acknowledge", "open_case"),
    "no_news": ("open_case", "acknowledge"),
    "cluster": ("request_alert", "open_case"),
    "coast_guard": ("open_coastguard", "acknowledge"),
}
SENDS_UNIT = "DISPATCH_UNIT_CANDIDATE"
ACTING = {"EVACUATE_ROUTE_CANDIDATE", "DISPATCH_UNIT_CANDIDATE", "SHELTER_CANDIDATE", "SEA_RESCUE_CANDIDATE"}


@dataclass(frozen=True)
class Config:
    interval_min: int = 10
    near_min: float = 10.0
    not_sent_min: float = 5.0
    silent_grace_min: float = 10.0
    idle_extra_min: float = 15.0
    callback_age_min: float = 15.0
    cluster_k: int = 3
    cluster_radius_m: float = 500.0
    cluster_window_min: float = 20.0
    coast_guard_min: float = 10.0
    due_min: float = 10.0


def _m(a: datetime, b: datetime) -> float:
    return (a - b) / timedelta(minutes=1)


def _dist(a: tuple[float, float], b: tuple[float, float]) -> float:
    k = math.pi / 180
    x = (b[0] - a[0]) * k * math.cos((a[1] + b[1]) / 2 * k)
    y = (b[1] - a[1]) * k
    return 6_371_000 * math.hypot(x, y)


def n(k: int, one: str, many: str) -> str:
    """Greek number agreement: "1 περιστατικό", "3 περιστατικά"."""
    return f"{k} {one if k == 1 else many}"


def _where(c: dict[str, Any]) -> str:
    return f"{c['id']} {c['label']}" if c.get("label") else c["id"]


def finding(kind: str, key: str, text: str, numbers: dict[str, Any], severity: int, case_ids=(), unit_ids=(),
            landing_ids=(), point: tuple[float, float] | None = None) -> dict[str, Any]:
    fid = kind + ":" + hashlib.sha1(key.encode()).hexdigest()[:8]
    return {"id": fid, "kind": kind, "case_ids": sorted(case_ids), "unit_ids": sorted(unit_ids),
            "landing_ids": sorted(landing_ids), "numbers": numbers, "severity": severity, "text_el": text,
            "allowed": list(ALLOWED[kind]), "default": ALLOWED[kind][0], "point": list(point) if point else None}


def front_no_decision(s: dict[str, Any], now: datetime, cfg: Config) -> list[dict[str, Any]]:
    rows = [c for c in s["cases"] if c["status"] in ("proposed", "failed") and c["decision"] in ACTING
            and c["minutes_left"] is not None and c["minutes_left"] <= cfg.near_min]
    if not rows:
        return []
    rows.sort(key=lambda c: c["minutes_left"])
    def left(c: dict[str, Any]) -> str:
        return "έχει φτάσει" if c["minutes_left"] <= 0 else f"{c['minutes_left']:.0f}′"
    detail = "; ".join(f"{_where(c)}: {left(c)}" for c in rows[:6])
    passed = sum(c["minutes_left"] <= 0 for c in rows)
    there = f" ({passed} με το μέτωπο ήδη εκεί)" if passed else ""
    text = (f"{n(len(rows), 'περιστατικό που κινεί', 'περιστατικά που κινούν')} ανθρώπους "
            f"είναι χωρίς απόφαση χειριστή με το μέτωπο σε "
            f"{cfg.near_min:.0f}′ ή λιγότερο{there}. {detail}.")
    return [finding("front_no_decision", "all", text, {"cases": len(rows), "passed": passed}, 95 + 3 * len(rows),
                    case_ids=[c["id"] for c in rows])]


def approved_not_sent(s: dict[str, Any], now: datetime, cfg: Config) -> list[dict[str, Any]]:
    rows = [c for c in s["cases"] if c["status"] in ("approved", "changed") and c["decision"] == SENDS_UNIT
            and not c["missions"] and c["acted_at"] is not None and _m(now, c["acted_at"]) >= cfg.not_sent_min]
    if not rows:
        return []
    wait = max(_m(now, c["acted_at"]) for c in rows)
    text = (f"{n(len(rows), 'περιστατικό εγκρίθηκε', 'περιστατικά εγκρίθηκαν')} για αποστολή μονάδας αλλά δεν "
            f"έχει σταλεί μονάδα (μεγαλύτερη αναμονή "
            f"{wait:.0f}′). " + "; ".join(_where(c) for c in rows[:6]) + ".")
    return [finding("approved_not_sent", "all", text, {"cases": len(rows), "wait_min": round(wait)},
                    85 + 3 * len(rows), case_ids=[c["id"] for c in rows])]


def idle_units(s: dict[str, Any], now: datetime, cfg: Config) -> list[dict[str, Any]]:
    free = [u for u in s["units"] if u["state"] in ("available", "unknown")]
    waiting = [c for c in s["cases"] if c["decision"] == SENDS_UNIT and c["open"] and not c["active_missions"]
               and _m(now, c["time"]) >= cfg.not_sent_min]
    if not free or not waiting:
        return []
    wait = max(_m(now, c["time"]) for c in waiting)
    text = (f"{n(len(free), 'μονάδα ξηράς είναι διαθέσιμη', 'μονάδες ξηράς είναι διαθέσιμες')} ενώ "
            + n(len(waiting), "περιστατικό «αποστολή μονάδας» περιμένει", "περιστατικά «αποστολή μονάδας» περιμένουν")
            + " "
            f"χωρίς μονάδα (μεγαλύτερη αναμονή {wait:.0f}′). " + "; ".join(_where(c) for c in waiting[:6]) + ".")
    return [finding("idle_units", "all", text, {"units": len(free), "cases": len(waiting), "wait_min": round(wait)},
                    70 + 4 * len(waiting), case_ids=[c["id"] for c in waiting], unit_ids=[u["id"] for u in free])]


def unit_silent(s: dict[str, Any], now: datetime, cfg: Config) -> list[dict[str, Any]]:
    out = []
    for m in s["missions"]:
        if m["phase"] not in ("assigned", "en_route"):
            continue
        late = _m(now, m["arrive"])
        if late < cfg.silent_grace_min:
            continue
        text = (f"Η μονάδα {m['unit']} (περιστατικό {m['case']}) δεν έχει αναφέρει άφιξη {late:.0f}′ μετά την "
                f"εκτιμώμενη ώρα άφιξης {m['arrive'].strftime('%H:%M')}. Τελευταία κατάσταση: {m['label_el']}.")
        out.append(finding("unit_silent", m["id"], text, {"late_min": round(late)}, 80 + min(20, round(late)),
                           case_ids=[m["case"]], unit_ids=[m["unit"]]))
    return out


def unit_idle(s: dict[str, Any], now: datetime, cfg: Config) -> list[dict[str, Any]]:
    out = []
    for m in s["missions"]:
        if m["phase"] != "on_scene" or m["since"] is None:
            continue
        over = _m(now, m["since"]) - m["on_scene_min"]
        if over < cfg.idle_extra_min:
            continue
        stay = _m(now, m["since"])
        text = (f"Η μονάδα {m['unit']} είναι επί τόπου στο {m['case']} εδώ και {stay:.0f}′ "
                f"(σχέδιο {m['on_scene_min']:.0f}′) χωρίς αναφορά για το τι βρήκε.")
        out.append(finding("unit_idle", m["id"], text, {"on_scene_min": round(stay), "planned_min": m["on_scene_min"]},
                           65 + min(20, round(over)), case_ids=[m["case"]], unit_ids=[m["unit"]]))
    return out


def unit_down(s: dict[str, Any], now: datetime, cfg: Config) -> list[dict[str, Any]]:
    out = []
    for m in s["missions"]:
        if m["phase"] != "out_of_service":
            continue
        others = [x for x in s["missions"] if x["case"] == m["case"] and x["id"] != m["id"]
                  and x["phase"] in ("assigned", "en_route", "on_scene")]
        case = next((c for c in s["cases"] if c["id"] == m["case"]), None)
        if others or case is None or not case["open_or_handled"]:
            continue
        text = (f"Η μονάδα {m['unit']} ανέφερε βλάβη στην αποστολή για το {m['case']} και καμία άλλη μονάδα δεν "
                "πηγαίνει εκεί.")
        out.append(finding("unit_down", m["id"], text, {}, 90, case_ids=[m["case"]], unit_ids=[m["unit"]]))
    return out


def callback_aging(s: dict[str, Any], now: datetime, cfg: Config) -> list[dict[str, Any]]:
    rows = [c for c in s["cases"] if c["callback_sent"] is not None and not c["callback_reply"]
            and not c["callback_cancelled"] and c["open"] and _m(now, c["callback_sent"]) >= cfg.callback_age_min]
    if not rows:
        return []
    wait = max(_m(now, c["callback_sent"]) for c in rows)
    text = (f"{n(len(rows), 'επανάκληση', 'επανακλήσεις')} χωρίς απάντηση για {cfg.callback_age_min:.0f}′ ή "
            f"περισσότερο (μεγαλύτερη "
            f"αναμονή {wait:.0f}′). " + "; ".join(_where(c) for c in rows[:6]) + ".")
    return [finding("callback_aging", "all", text, {"callbacks": len(rows), "wait_min": round(wait)},
                    60 + 4 * len(rows), case_ids=[c["id"] for c in rows])]


def no_news(s: dict[str, Any], now: datetime, cfg: Config) -> list[dict[str, Any]]:
    # A call-back already waiting for a reply is callback_aging's, not this one's.
    rows = [c for c in s["cases"] if c.get("no_news_min") is not None
            and not (c["callback_sent"] is not None and not c["callback_reply"] and not c["callback_cancelled"])]
    if not rows:
        return []
    longest = max(c["no_news_min"] for c in rows)
    text = (f"{n(len(rows), 'περιστατικό', 'περιστατικά')} που περιμένουν βοήθεια χωρίς κανένα νέο εδώ και 20′ ή "
            f"περισσότερο (το μεγαλύτερο {longest:.0f}′). Ερώτηση: «Είστε ακόμα στο ίδιο σημείο;». "
            + "; ".join(_where(c) for c in rows[:6]) + ".")
    return [finding("no_news", "all", text, {"cases": len(rows), "quiet_min": round(longest)},
                    55 + 4 * len(rows), case_ids=[c["id"] for c in rows])]


def cluster(s: dict[str, Any], now: datetime, cfg: Config) -> list[dict[str, Any]]:
    # People asking for help: not "we are safe", information or rumours.
    new = [c for c in s["cases"] if c["status"] in ("proposed", "failed") and c["point"] is not None
           and (c["decision"] in ACTING or c["decision"] == "CALLBACK")
           and 0 <= _m(now, c["time"]) <= cfg.cluster_window_min]
    out = []
    rest = sorted(new, key=lambda c: (c["time"], c["id"]))
    while len(rest) >= cfg.cluster_k:
        groups = [(c, [o for o in rest if _dist(c["point"], o["point"]) <= cfg.cluster_radius_m]) for c in rest]
        centre, best = max(groups, key=lambda g: len(g[1]))
        if len(best) < cfg.cluster_k:
            break
        known = [c["people"] for c in best if c["people"]]
        text = (f"{len(best)} νέα ανοιχτά περιστατικά σε {cfg.cluster_window_min:.0f}′ σε απόσταση έως "
                f"{cfg.cluster_radius_m:.0f} m: " + "; ".join(_where(c) for c in best) + "."
                + (f" Δηλωμένα άτομα: {sum(known)}." if known else ""))
        out.append(finding("cluster", best[0]["id"], text, {"cases": len(best), "people": sum(known)},
                           75 + 5 * len(best), case_ids=[c["id"] for c in best], point=centre["point"]))
        gone = {c["id"] for c in best}
        rest = [c for c in rest if c["id"] not in gone]
    return out



def coast_guard(s: dict[str, Any], now: datetime, cfg: Config) -> list[dict[str, Any]]:
    sea = s.get("sea") or {}
    since = sea.get("waiting_since")
    if since is None:
        return []
    wait = _m(now, since)
    if wait < cfg.coast_guard_min:
        return []
    text = (f"Μήνυμα από τη θάλασσα ή την ακτή περιμένει {wait:.0f}′ (από τις {since.strftime('%H:%M')}) χωρίς αίτημα "
            f"στο Λιμενικό· ανοιχτά περιστατικά θάλασσας: {sea.get('cases', 0)}.")
    return [finding("coast_guard", "sea", text, {"wait_min": round(wait), "cases": sea.get("cases", 0)},
                    90 + min(10, round(wait / 5)))]


DETECTORS = (front_no_decision, approved_not_sent, idle_units, unit_silent, unit_idle, unit_down, callback_aging,
             no_news, cluster, coast_guard)


def detect(snapshot: dict[str, Any], now: datetime, cfg: Config | None = None) -> list[dict[str, Any]]:
    cfg = cfg or Config()
    found = [f for d in DETECTORS for f in d(snapshot, now, cfg)]
    return sorted(found, key=lambda f: (-f["severity"], f["id"]))
