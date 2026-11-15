"""People counted from the people a sender lists: "είμαι με τα δύο μωρά μου" = 3. Pure code, never the model.

Used only when the message gives no total. Not used when:
  - someone writes for others ("η μάνα μου είναι στο σπίτι"): the sender is not one of the people;
  - the sender writes "we" without a number ("είμαστε με τα παιδιά"): how many "we" are is unknown;
  - a group is named without a number ("με τα παιδιά μου"): unknown, so the call-back keeps asking.
The result carries the words it was counted from, and the case says the count was derived, to confirm.
"""
import re

from palladion.field.extract import WORDS, fold

NUM = r"(\d{1,2}|" + "|".join(sorted((w for w in WORDS if WORDS[w] >= 2), key=len, reverse=True)) + ")"
# Folded (lower case, no accents). Plural people who can be counted when a number comes with them.
PLURAL = (r"παιδια|παιδακια|μωρα|εγγονια|ανιψια|αδερφια|ξαδερφια|φιλους|φιλες|γειτονες|ηλικιωμενους|"
          r"kids|children|babies|friends|neighbours|neighbors|grandchildren")
ONE = (r"(?:τη|την|τον|το|ενα|μια|μια) (?:γυναικα|συζυγο|μητερα|μανα|αδερφη|κορη|γιαγια|φιλη|γειτονισσα|αντρα|πατερα|"
       r"αδερφο|γιο|παππου|φιλο|γειτονα|μωρο|παιδι|εγγονο|εγγονη)|my (?:wife|husband|mother|mum|mom|father|dad|"
       r"sister|brother|son|daughter|baby|friend|neighbou?r|grandmother|grandfather)")
COUNTED = re.compile(rf"\b(?:με|with|και|and)(?: (?:τα|τους|τις|my|our))? {NUM} (?:{PLURAL})\b")
SINGLE = re.compile(rf"\b(?:με|with|και|and) (?:{ONE})\b")
UNCOUNTED = re.compile(rf"\b(?:με|with|και|and) (?:τα|τους|τις|my|our) (?:{PLURAL})\b")
SELF = re.compile(r"\b(?:ειμαι|βρισκομαι|μενω|i am|i'm|im)\b")
WE = re.compile(r"\b(?:ειμαστε|βρισκομαστε|we are|we're)\b")


def _n(word: str) -> int:
    return int(word) if word.isdigit() else WORDS[word]


def derive(text: str, reporter: str) -> tuple[int, str] | None:
    """(total, the words it came from), or None when the message does not let code count."""
    if reporter != "self":
        return None
    f = fold(text)
    if WE.search(f) or not SELF.search(f) or UNCOUNTED.search(f):
        return None
    total, spans = 1, []
    for m in COUNTED.finditer(f):
        total += _n(m.group(1))
        spans.append(m.span())
    for m in SINGLE.finditer(f):
        if not any(a <= m.start() < b for a, b in spans):
            total += 1
            spans.append(m.span())
    if total == 1:
        return None                  # only the sender: no list to count from
    a, b = min(s[0] for s in spans), max(s[1] for s in spans)
    return total, text[a:b]
