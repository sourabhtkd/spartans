import sqlite3
from pathlib import Path

from langgraph.checkpoint.sqlite import SqliteSaver

from codeforge.entrypoint import submit
from codeforge.pipeline import build_graph
from codeforge.privacy.pii import generate_verhoeff_check_digit
from codeforge.state import GeneratedCode, GeneratedTests, Requirements, RouteDecision
from tests.fakes import FakeLLM

SECRET = "sk-abcdefghijklmnop1234"


def _aadhaar() -> str:
    base = "23456789012"
    return base + str(generate_verhoeff_check_digit(base))


def test_checkpoint_db_never_contains_raw_pii(tmp_path: Path, monkeypatch):
    fake = FakeLLM()
    fake.set_response("router", RouteDecision(kind="python_function"))
    fake.set_response("requirements", Requirements(function_name="f"))
    fake.set_response("codegen", GeneratedCode(code="def f():\n    return 1\n"))
    fake.set_response(
        "testgen",
        GeneratedTests(
            tests="from solution import f\n\ndef test_a():\n    assert f() == 1\n    assert f() == 1\n    assert f() == 1\n"
        ),
    )
    for m in ("router", "requirements", "codegen", "testgen"):
        monkeypatch.setattr(f"codeforge.agents.{m}.resilient_invoke", fake)

    db = tmp_path / "ckpt.db"
    conn = sqlite3.connect(str(db), check_same_thread=False)
    graph = build_graph(checkpointer=SqliteSaver(conn))
    aadhaar = _aadhaar()
    submit(
        f"Write function f. my key {SECRET} aadhaar {aadhaar}",
        thread_id="th_priv_1",
        graph=graph,
    )
    conn.commit()
    conn.close()
    raw = b"".join(p.read_bytes() for p in tmp_path.glob("ckpt.db*"))
    assert SECRET.encode() not in raw
    assert aadhaar.encode() not in raw
    assert b"[SECRET]" in raw
