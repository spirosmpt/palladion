from fastapi.testclient import TestClient

from palladion.api.app import create_app
from palladion.config import Settings
from palladion.i18n import place, tr, translate


def test_code_sentences_in_english_and_people_words_untouched():
    assert tr("Κατά τη λήψη του μηνύματος το μέτωπο απείχε περίπου 7′.") == \
        "When the message came in, the front was about 7′ away."
    assert tr("1 άτομα.") == "1 person." and tr("Ευάλωτοι: ηλικιωμένοι, χωρίς όχημα.") == \
        "Vulnerable: elderly, no vehicle."
    assert tr("Διαιτητής: Οι πράκτορες δεν διαφωνούν.") == "Arbiter: The agents do not disagree."
    msg = "Είμαστε στο σπίτι, η μητέρα μου δεν περπατάει"
    assert tr(msg) == msg                                          # a message is never touched
    out = translate({"text": "Δεν έχουν όχημα.", "reasons": ["Δεν έχουν όχημα."],
                     "location": {"label": "Ταϋγέτου, Νέος Βουτζάς"}})
    assert out["text"] == "Δεν έχουν όχημα." and out["reasons"] == ["They have no vehicle."]
    assert out["location"]["label"] == "Taygetou, Neos Voutzas" and place("Μάτι") == "Mati"


def test_the_desk_asks_for_english_with_a_header(tmp_path):
    s = Settings(case_worker=False, supervisor_agent=False, llm_provider="replay", llm_cache_dir=tmp_path)
    with TestClient(create_app(s)) as c:
        el = c.get("/api/handover").json()
        en = c.get("/api/handover", headers={"X-Lang": "en"}).json()
        assert el["note_el"].startswith("Χρόνος σεναρίου")
        assert en["note_el"] == "Scenario time; anything after this time does not count."
