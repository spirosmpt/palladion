"""Text normalisation before the rules: SMS shorthand, everyday words and common misspellings, in one reading.

The rules (regular expressions over lower-case text without accents) are written in standard spelling. Messages in a
fire are not: "δν βρισκουμε τη μαμα μου", "ιμαστε καλα", "βοηθειααα", "φοτια στο σπητι". This turns such words into
the spelling the rules expect, word by word:
  - a letter repeated three or more times is written once ("βοηθειααα" -> "βοηθεια"; a double letter stays);
  - a short list of shorthand and everyday words ("δν" -> "δεν", "τπτ" -> "τιποτα", "μαμα" -> "μητερα");
  - a short list of misspellings of the words the rules look for (ο/ω, ι/η/ει/οι), on whole words only.
It never touches numbers, names or places, and it keeps a map back to the original, so a quote is always the sender's
own words, as written. What the agents read is unchanged: they see the original text.
"""
import re
import unicodedata
from dataclasses import dataclass


def fold(text: str) -> str:
    """Lower case without accents, one character for each character (as field.extract.fold)."""
    out = []
    for ch in text:
        base = "".join(c for c in unicodedata.normalize("NFD", ch.lower()) if unicodedata.category(c) != "Mn")
        out.append(base[:1] if base else ch)
    return "".join(out)


# Whole words, after folding and collapsing repeats. Only words whose meaning is not in doubt.
WORDS = {
    # shorthand
    "δν": "δεν", "τπτ": "τιποτα", "τποτα": "τιποτα", "τιποτε": "τιποτα", "κλ": "καλα", "ντξ": "ενταξει",
    "νταξει": "ενταξει", "νταξ": "ενταξει", "ενταξ": "ενταξει", "γρ": "γρηγορα", "pls": "please", "plz": "please",
    # everyday words for relatives (the rules know the standard ones)
    "μαμα": "μητερα", "μανουλα": "μητερα", "μαμακα": "μητερα", "μπαμπας": "πατερας", "μπαμπα": "πατερα",
    "μπαμπακα": "πατερα", "γιαγιακα": "γιαγια", "παππουλη": "παππου", "παππουλης": "παππους",
    # misspellings of words the rules look for
    "ιμαστε": "ειμαστε", "ημαστε": "ειμαστε", "ειμαστ": "ειμαστε", "ιμαι": "ειμαι", "ημαι": "ειμαι",
    "βοηθια": "βοηθεια", "βοιθεια": "βοηθεια", "βοιθια": "βοηθεια", "βοηθεα": "βοηθεια", "βοηθιεα": "βοηθεια",
    "βοιθηστε": "βοηθηστε", "βοηθειστε": "βοηθηστε", "βοηθιστε": "βοηθηστε", "βοηθησται": "βοηθηστε",
    "φοτια": "φωτια", "φωτεια": "φωτια", "φοτεια": "φωτια", "φωτηα": "φωτια",
    "φλωγες": "φλογες", "φλογαις": "φλογες", "καπνως": "καπνος", "σπητι": "σπιτι",
    "βρικαμε": "βρηκαμε", "βρεθικε": "βρεθηκε", "βρεθικαν": "βρεθηκαν", "χαθικε": "χαθηκε", "χαθικαν": "χαθηκαν",
    "αγνοητε": "αγνοειται", "αγνοειτε": "αγνοειται", "αγνοιτε": "αγνοειται",
    "ψαχνο": "ψαχνω", "ψαχνουμαι": "ψαχνουμε", "ψαχνωυμε": "ψαχνουμε", "σηκονει": "σηκωνει", "απανταη": "απανταει",
    "γριγορα": "γρηγορα", "γρηγωρα": "γρηγορα", "ελλατε": "ελατε", "ασφαλεης": "ασφαλεις",
    "διασωθικε": "διασωθηκε", "διασωθικαν": "διασωθηκαν", "βγικαμε": "βγηκαμε", "βγικαν": "βγηκαν",
    "ολλοι": "ολοι", "ωλοι": "ολοι", "καλλα": "καλα", "καλά": "καλα",
}
# Whole-word patterns for families of forms.
PATTERNS = [
    (re.compile(r"εγκλ[οω]β[ιηυ]σμ[εαη]ν(\w*)"), r"εγκλωβισμεν\1"),
    (re.compile(r"παγιδ[εε]υμ[εη]ν(\w*)"), r"παγιδευμεν\1"),
]
REPEAT = re.compile(r"(\w)\1{2,}")
WORD = re.compile(r"[^\W\d_]+", re.UNICODE)


@dataclass
class Normalised:
    text: str
    start: list[int]          # for each character of `text`, where it starts in the original
    end: list[int]            # and where it ends (exclusive)

    def span(self, s: int, e: int) -> tuple[int, int]:
        """The original span for a span of the normalised text."""
        if s >= e or not self.text:
            return s, s
        return self.start[s], self.end[e - 1]


def _word(w: str) -> str:
    w2 = REPEAT.sub(r"\1", w)
    if w2 in WORDS:
        return WORDS[w2]
    for pat, rep in PATTERNS:
        if pat.fullmatch(w2):
            return pat.sub(rep, w2)
    return w2


def normalise(text: str) -> Normalised:
    f = fold(text)                                  # one character per character: positions match the original
    out: list[str] = []
    start: list[int] = []
    end: list[int] = []
    i = 0
    for m in WORD.finditer(f):
        for k in range(i, m.start()):              # what lies between words is copied as it is
            out.append(f[k])
            start.append(k)
            end.append(k + 1)
        rep = _word(m.group(0))
        if rep == m.group(0):
            for k in range(m.start(), m.end()):
                out.append(f[k])
                start.append(k)
                end.append(k + 1)
        else:                                       # every character of a replaced word maps to the whole word
            for ch in rep:
                out.append(ch)
                start.append(m.start())
                end.append(m.end())
        i = m.end()
    for k in range(i, len(f)):
        out.append(f[k])
        start.append(k)
        end.append(k + 1)
    return Normalised("".join(out), start, end)


def norm(text: str) -> str:
    """The normalised text alone, for rules that only ask whether something is said."""
    return normalise(text).text
