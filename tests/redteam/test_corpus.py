from pathlib import Path

import pytest
import yaml

from codeforge.config import current_identity
from codeforge.policy.code_policy import evaluate_code_policy
from codeforge.privacy.pii import mask_text

CORPUS_PATH = Path(__file__).resolve().parent.parent.parent / "redteam" / "corpus.yaml"
CASES = yaml.safe_load(CORPUS_PATH.read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_adversarial_corpus_case(case: dict):
    category = case["category"]
    inp = case["input"]
    expected = case["expected_verdict"]

    if category in ("destructive", "obfuscation", "dunder_escape", "approval_required", "allowed_safe", "bypass"):
        decision = evaluate_code_policy(inp)
        assert (
            decision.verdict == expected
        ), f"Expected {expected} but got {decision.verdict} for input:\n{inp}\nReasons: {decision.reasons}"

    elif category == "prompt_injection":
        ident = current_identity()
        assert ident.role == "student"

    elif category == "pii_leakage":
        masked = mask_text(inp)
        assert any(
            tag in masked for tag in ["[SECRET]", "[EMAIL]", "[CARD]", "[AADHAAR]", "[PHONE]"]
        )

    else:
        pytest.fail(f"unknown corpus category '{category}': case would pass vacuously")
