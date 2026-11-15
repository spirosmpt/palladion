"""Personal data is masked before any model reads a message; the operator still sees the original.

What is masked: phone numbers, e-mail addresses, Greek tax and social-security numbers (ΑΦΜ 9 digits, ΑΜΚΑ 11),
identity cards (ΑΔΤ: two letters + 6 digits), IBAN, card numbers and vehicle plates. House numbers, counts, ages and
times are not: "Κυκλάδων 12", "είμαστε 4", "82 χρονών", "18:40" stay as they are.

The mask keeps the length and the separators ("694 123 4567" -> "••• ••• ••••"), so a quote the agents copy from the
masked text sits at the same place in the original, and the operator's highlighting still lines up.
"""
import re

MASK = "•"

# Order matters: longer and more specific shapes first.
PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("email", re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")),
    ("iban", re.compile(r"\bGR\d{2}(?:[ ]?\d{4}){5}[ ]?\d{3}\b", re.IGNORECASE)),
    ("card", re.compile(r"\b(?:\d{4}[ -]?){3}\d{4}\b")),
    # Greek phones: mobile 69x, landline 2xx, with or without +30/0030, spaces, dots or dashes inside.
    ("phone", re.compile(r"(?<![\w+])(?:(?:\+|00)\s?30[\s.-]?)?(?:69\d|2\d{2})(?:[\s.-]?\d){7}(?!\d)")),
    # Other long digit runs written as a phone (foreign numbers of relatives abroad: +44 7700 900123).
    ("phone", re.compile(r"(?<![\w])\+\d{1,3}(?:[\s.-]?\d){6,12}(?!\d)")),
    ("amka", re.compile(r"(?<!\d)\d{11}(?!\d)")),
    ("afm", re.compile(r"(?i)(?:α\.?φ\.?μ\.?|afm)\s*:?\s*(?P<v>\d{9})\b")),
    ("id_card", re.compile(r"(?i)(?:α\.?δ\.?τ\.?|ταυτότητα)\s*:?\s*(?P<v>[A-ZΑ-Ω]{1,2}[\s-]?\d{6})\b")),
    # Plates: three capital letters (Greek or Latin look-alikes) and four digits: "ΥΖΑ-1234", "IKA 4521".
    ("plate", re.compile(r"\b[ΑΒΕΖΗΙΚΜΝΟΡΤΥΧABEZHIKMNOPTYX]{3}[\s-]?\d{4}\b")),
]
LABEL_EL = {"email": "email", "iban": "IBAN", "card": "αριθμός κάρτας", "phone": "τηλέφωνο", "amka": "ΑΜΚΑ",
            "afm": "ΑΦΜ", "id_card": "ταυτότητα", "plate": "πινακίδα"}


def _mask(s: str) -> str:
    return "".join(ch if ch in " .-+:/" else MASK for ch in s)


def redact(text: str) -> tuple[str, list[str]]:
    """The masked text (same length) and the kinds found, in order, each once."""
    out, kinds = text, []
    for kind, pat in PATTERNS:
        def sub(m: re.Match[str], kind: str = kind) -> str:
            if kind not in kinds:
                kinds.append(kind)
            if "v" in m.re.groupindex:     # keep the label ("ΑΦΜ:"), mask the value
                a, b = m.span("v")
                whole = m.group(0)
                return whole[: a - m.start()] + _mask(m.group("v")) + whole[b - m.start():]
            return _mask(m.group(0))
        out = pat.sub(sub, out)
    return out, kinds
