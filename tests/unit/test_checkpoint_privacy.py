from pathlib import Path

from codeforge.entrypoint import submit
from codeforge.privacy.pii import generate_verhoeff_check_digit
from tests.helpers import memory_graph, patch_llm, scripted_fake

SECRET = "sk-abcdefghijklmnop1234"


def _aadhaar() -> str:
    base = "23456789012"
    return base + str(generate_verhoeff_check_digit(base))


def test_checkpoint_db_never_contains_raw_pii(tmp_path: Path, monkeypatch):
    patch_llm(monkeypatch, scripted_fake("def f(x):\n    return x + 1\n"))
    aadhaar = _aadhaar()

    graph = memory_graph(str(tmp_path / "ckpt.db"))
    submit(f"Write function f. my key {SECRET} aadhaar {aadhaar}", thread_id="th_priv_1", graph=graph)

    raw = b"".join(path.read_bytes() for path in tmp_path.glob("ckpt.db*"))
    assert SECRET.encode() not in raw
    assert aadhaar.encode() not in raw
    assert b"[SECRET]" in raw
