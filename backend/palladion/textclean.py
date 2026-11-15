r"""Text from outside is cleaned once, where it enters (an operator's entry, an answer, a crew report).

  - unpaired surrogates (possible in JSON as "\ud83d") become U+FFFD: they cannot be written to the log as UTF-8;
  - control characters other than newline and tab are dropped (terminal colour codes, NUL, bell);
  - zero-width and direction marks are dropped, so "Κυ<ZWSP>κλάδων" is still the street Κυκλάδων;
  - Unicode is normalised to NFC, so the same letter is always the same code points.
Nothing else is changed: case, accents, spelling and emoji stay as written.
"""
import re
import unicodedata

_CONTROL = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")
_ZERO_WIDTH = re.compile("[\u200b-\u200f\u2060\ufeff\u202a-\u202e]")
_SURROGATE = re.compile("[\ud800-\udfff]")      # a Python str holds surrogates only when they are unpaired


def clean(text: str) -> str:
    text = _SURROGATE.sub("\ufffd", text)
    text = _ZERO_WIDTH.sub("", _CONTROL.sub("", text))
    return unicodedata.normalize("NFC", text)
