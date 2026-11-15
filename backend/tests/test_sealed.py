import yaml

from palladion.config import REPO_ROOT
from palladion.eval.run_sealed import VALID


def test_template_examples_use_known_decisions_and_stay_out_of_git():
    t = yaml.safe_load((REPO_ROOT / "eval" / "sealed" / "mati_2018_sealed.template.yaml").read_text(encoding="utf-8"))
    examples = t["messages"]
    assert examples and all(m["example"] for m in examples)
    assert all(set(m["expected"]["decision"]) <= VALID for m in examples)
    assert "eval/sealed/mati_2018_sealed.yaml" in (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")
