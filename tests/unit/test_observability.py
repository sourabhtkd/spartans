import json

import pytest

from codeforge.observability import get_logger


def test_logger_masks_pii(capsys: pytest.CaptureFixture[str]):
    logger = get_logger("test_obs")
    raw_secret = "sk-abcdef1234567890XYZ"
    logger.info("user_event", secret_key=raw_secret, email="user@domain.com")

    captured = capsys.readouterr()
    output = captured.err or captured.out
    assert raw_secret not in output
    assert "user@domain.com" not in output
    assert "[SECRET]" in output
    assert "[EMAIL]" in output

    parsed = json.loads(output.strip().splitlines()[-1])
    assert parsed["secret_key"] == "[SECRET]"
    assert parsed["email"] == "[EMAIL]"
