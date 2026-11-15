"""112 writer: turns a warning that code has already decided into a short message in Greek and English.

The agent only writes. Code chooses the area, the urgency and the instruction (engines/warnings.py),
and checks the text: length, the area named, the instruction present, the destination and roads it
was given and no other place. If the text fails twice, a fixed template is used, so a warning is
never held up by the model.
"""
import re
from typing import Any

from pydantic import BaseModel, Field

from palladion.engines.warnings import Action, Instruction, Threat
from palladion.geo.gazetteer import phonetic
from palladion.llm.client import LLM, LLMError

MAX_CHARS = 300          # per language; a cell broadcast in Greek (UCS-2) fits about 600 characters in total


class AlertText(BaseModel):
    el: str = Field(description="the message in Greek")
    en: str = Field(description="the same message in English")


SYSTEM = f"""You write public emergency warnings (112 cell broadcast) for a wildfire in Greece.
The decision has been made already. Write it, do not change it.

Rules:
- Greek first ("el"), then the same content in English ("en"). Each at most {MAX_CHARS} characters.
- Start the Greek with "112:" and the English with "112:".
- Name the area exactly as given (in English, in Latin letters: Μάτι -> Mati). Say how soon the fire arrives, as given.
- Give exactly the instruction you are given, in plain imperative words:
  * evacuate: leave now towards the destination given, by the roads given. Name them.
  * go_to_coast: do NOT use a car; walk now to the beach given, described exactly as given.
    Never say "the coast" or "the nearest beach": the nearest shore may be a cliff.
  * shelter: do NOT use a car; stay inside a solid (concrete) building, close doors and windows.
- Do not add any other place, road, number, advice or reason. No phone numbers, no links.
- Calm and direct. No exclamation marks, no capital-letter shouting except "112".
"""


def facts(t: Threat) -> dict[str, Any]:
    ins = t.instruction
    when = "σε λιγότερο από 15 λεπτά" if t.band[0] == 0 else f"σε {t.band[0]}–{t.band[1]} λεπτά"
    when_en = "in less than 15 minutes" if t.band[0] == 0 else f"in {t.band[0]}-{t.band[1]} minutes"
    out: dict[str, Any] = {"area": t.settlement, "fire_arrives": when, "fire_arrives_en": when_en,
                           "instruction": ins.action.value}
    if ins.action == Action.evacuate:
        out["destination"] = ins.destination
        out["roads"] = ins.roads
    if ins.action == Action.go_to_coast and ins.coast_name:
        out["beach"] = ins.coast_name
    elif ins.action == Action.go_to_coast and ins.coast_road:
        out["beach"] = f"the beach at the end of {ins.coast_road} street (name the street)"
    return out


_DIGRAPHS = [("ου", "ou"), ("αυ", "av"), ("ευ", "ev"), ("μπ", "b"), ("ντ", "nt"), ("γκ", "gk"), ("γγ", "ng")]
_LETTERS = dict(zip("αβγδεζηθικλμνξοπρστυφχψωςάέήίόύώϊϋΐΰ",
                    ["a", "v", "g", "d", "e", "z", "i", "th", "i", "k", "l", "m", "n", "x", "o", "p", "r", "s", "t",
                     "y", "f", "ch", "ps", "o", "s", "a", "e", "i", "i", "o", "y", "o", "i", "y", "i", "y"],
                    strict=True))


def latin(name: str) -> str:
    """Greek place name in Latin letters, close to ELOT 743 (Μάτι -> Mati, Ραφήνα -> Rafina)."""
    import unicodedata
    out = []
    for word in name.split():
        w = "".join(c for c in unicodedata.normalize("NFD", word.lower()) if unicodedata.category(c) != "Mn")
        for g, r in _DIGRAPHS:
            w = w.replace(g, r)
        w = "".join(_LETTERS.get(c, c) for c in w)
        out.append(w[:1].upper() + w[1:])
    return " ".join(out)


def template(t: Threat) -> AlertText:
    f, ins = facts(t), t.instruction
    if ins.action == Action.evacuate:
        roads = ", ".join(ins.roads)
        el = (f"112: Πυρκαγιά πλησιάζει την περιοχή {t.settlement}. Φτάνει {f['fire_arrives']}. "
              f"Απομακρυνθείτε τώρα προς {ins.destination}" + (f" μέσω: {roads}." if roads else "."))
        roads_en = ", ".join(latin(r) for r in ins.roads)
        en = (f"112: Wildfire approaching {latin(t.settlement)}. It arrives {f['fire_arrives_en']}. "
              f"Leave now towards {latin(ins.destination or '')}" + (f" via: {roads_en}." if roads_en else "."))
    elif ins.action == Action.go_to_coast:
        if ins.coast_name:
            beach_el, beach_en = f"στην παραλία {ins.coast_name}", f"to {latin(ins.coast_name)} beach"
        else:
            road = ins.coast_road or ""
            beach_el = f"στην παραλία στο τέλος της οδού {road}"
            beach_en = f"to the beach at the end of {latin(road)} street"
        el = (f"112: Πυρκαγιά στην περιοχή {t.settlement}. Φτάνει {f['fire_arrives']}. Μη χρησιμοποιείτε "
              f"αυτοκίνητο. Πηγαίνετε τώρα με τα πόδια {beach_el}.")
        en = (f"112: Wildfire in {latin(t.settlement)}. It arrives {f['fire_arrives_en']}. Do not use a car. "
              f"Walk {beach_en} now.")
    else:
        el = (f"112: Πυρκαγιά στην περιοχή {t.settlement}. Φτάνει {f['fire_arrives']}. Μη χρησιμοποιείτε "
              "αυτοκίνητο. Μείνετε σε κτίριο από τσιμέντο, κλείστε πόρτες και παράθυρα.")
        en = (f"112: Wildfire in {latin(t.settlement)}. It arrives {f['fire_arrives_en']}. Do not use a car. "
              "Stay inside a concrete building and close doors and windows.")
    return AlertText(el=el, en=en)


def _words(text: str) -> list[str]:
    return [phonetic(w) for w in re.findall(r"[^\W\d_]+", text)]


def mentions(text: str, name: str) -> bool:
    """Greek declines names (Μαραθώνος / Μαραθώνα, Ραφήνα / Ραφήνας) and English transliterates them, so
    a name counts as mentioned when every long word of it appears with the same beginning, in any script."""
    words = _words(text)
    need = [w for w in _words(name) if len(w) >= 4]
    if not need:
        return phonetic(name) in "".join(words)

    def found(w: str) -> bool:
        k = max(4, len(w) - 2)
        return any(x[:k] == w[:k] for x in words if len(x) >= k)
    return all(found(w) for w in need)


ACTION_WORDS = {
    Action.evacuate: (["απομακρυν", "φυγ", "φυγετ", "εκκενω", "αναχωρ"], ["leave", "evacuate", "go"]),
    Action.go_to_coast: (["παραλι"], ["beach"]),
    Action.shelter: (["κτιρι", "μεινετ", "παραμειν"], ["building", "stay", "shelter"]),
}
NO_CAR = (["αυτοκινητ", "οχημα"], ["car", "vehicle", "drive"])


def _has(text: str, stems: list[str]) -> bool:
    import unicodedata
    t = "".join(c for c in unicodedata.normalize("NFD", text.lower()) if unicodedata.category(c) != "Mn")
    return any(s in t for s in stems)


def check(text: AlertText, t: Threat, other_places: list[str]) -> list[str]:
    """Problems with a written warning; empty when it says exactly what was decided."""
    ins: Instruction = t.instruction
    problems = []
    for lang, body in (("el", text.el), ("en", text.en)):
        if len(body) > MAX_CHARS:
            problems.append(f"{lang}: {len(body)} characters (limit {MAX_CHARS})")
        if not body.strip().startswith("112"):
            problems.append(f"{lang}: must start with 112")
        if not mentions(body, t.settlement):
            problems.append(f"{lang}: the area '{t.settlement}' is not named")
        if re.search(r"https?://|www\.|\+?\d[\d\s]{6,}", body):
            problems.append(f"{lang}: contains a link or a phone number")
        el_stems, en_stems = ACTION_WORDS[ins.action]
        if not _has(body, el_stems if lang == "el" else en_stems):
            problems.append(f"{lang}: the instruction '{ins.action.value}' is missing")
        if ins.action == Action.evacuate:
            for name in [ins.destination or "", *ins.roads]:
                if name and not mentions(body, name):
                    problems.append(f"{lang}: '{name}' is not mentioned")
        if ins.action == Action.go_to_coast:
            for place in (ins.coast_name, ins.coast_road):
                if place and not mentions(body, place):
                    problems.append(f"{lang}: '{place}' is not mentioned")
        if ins.action != Action.evacuate and not _has(body, NO_CAR[0] if lang == "el" else NO_CAR[1]):
            problems.append(f"{lang}: must say not to use a car")
        allowed = {t.settlement, ins.destination or "", ins.coast_name or "", ins.coast_road or "", *ins.roads}
        name_words = {w for a in allowed for w in re.findall(r"[^\W\d_]+", a)}
        shouting = [w for w in re.findall(r"[^\W\d_]{3,}", body) if w.isupper() and w not in name_words
                    and latin(w) not in {latin(x) for x in name_words}]
        if shouting:
            problems.append(f"{lang}: words in capitals ({', '.join(shouting[:3])}); write in normal case")
        for place in other_places:
            if place and place not in allowed and not any(mentions(a, place) for a in allowed if a) \
                    and mentions(body, place):
                problems.append(f"{lang}: names '{place}', which was not given")
    return problems


class WrittenAlert(BaseModel):
    text: AlertText
    source: str                     # "agent" | "template"
    problems: list[str] = Field(default_factory=list)   # problems of the agent's last attempt, if any
    call: dict | None = None


def write_alert(llm: LLM | None, t: Threat, other_places: list[str]) -> WrittenAlert:
    """Agent text if it passes the checks (one rewrite allowed), otherwise the template."""
    if llm is None:
        return WrittenAlert(text=template(t), source="template")
    import json
    user = "Write this warning.\n" + json.dumps(facts(t), ensure_ascii=False)
    problems: list[str] = []
    call = None
    for _ in range(2):
        try:
            out, rec = llm.run("alert_writer", SYSTEM, user, AlertText)
        except LLMError as exc:
            problems = [f"model: {exc}"]
            break
        from palladion.agents.intake import _call_dict
        call = _call_dict(rec)
        problems = check(out, t, other_places)
        if not problems:
            return WrittenAlert(text=out, source="agent", call=call)
        user = (user + "\n\nYour previous text had these problems; fix them:\n- " + "\n- ".join(problems)
                + "\nPrevious text: " + out.model_dump_json())
    return WrittenAlert(text=template(t), source="template", problems=problems, call=call)
