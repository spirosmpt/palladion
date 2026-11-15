from palladion.agents.credibility import _find
from palladion.field.extract import rules
from palladion.textnorm import norm, normalise


def test_shorthand_misspellings_and_repeats_in_standard_spelling():
    assert norm("Δν βρίσκουμε τη μαμά μου") == "δεν βρισκουμε τη μητερα μου"
    assert norm("ΒΟΗΘΕΙΑΑΑ!!! φοτιά στο σπήτι") == "βοηθεια!!! φωτια στο σπιτι"
    assert norm("ιμαστε εγκλοβισμένοι") == "ειμαστε εγκλωβισμενοι"
    assert norm("Κυκλάδων 12, Μάτι, 3 άτομα") == "κυκλαδων 12, ματι, 3 ατομα"       # names, numbers: unchanged
    assert norm("αλλά καλλα") == "αλλα καλα"                                        # a double letter stays


def test_a_quote_is_always_the_original_words():
    text = "Δν βρίσκουμε τη ΜΑΜΑ μου"
    n = normalise(text)
    i = n.text.index("μητερα")
    s, e = n.span(i, i + len("μητερα"))
    assert text[s:e] == "ΜΑΜΑ"


def test_the_rules_now_read_everyday_writing():
    assert _find(r"\b(ακουσα οτι)", "Άκουσα ότι καίγεται") == "Άκουσα ότι"
    found = rules("Βγήκαν όλοιιι, τους πήραμε")
    assert any(f.kind == "all_out" and f.quote == "Βγήκαν όλοιιι" for f in found)
