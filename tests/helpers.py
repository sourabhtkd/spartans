"""Helpers for the golden-set gate: graph construction, LLM fakes and cassette record/replay."""
import hashlib
import json
import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from langgraph.checkpoint.sqlite import SqliteSaver

import codeforge.pipeline.nodes as graph_module
from codeforge.llm import AllModelsDown
from codeforge.pipeline import build_graph
from codeforge.state import GeneratedCode, GeneratedTests, Requirements, RouteDecision
from tests.fakes import FakeLLM

EVALS_DIR = Path(__file__).resolve().parent.parent / "evals"
CASSETTE_DIR = EVALS_DIR / "cassettes"
REFERENCE_DIR = EVALS_DIR / "reference_tests"
AGENT_MODULES = ("router", "requirements", "codegen", "testgen")

GOOD_TESTS = (
    "from solution import f\n\n"
    "def test_a():\n    assert f(1) == 2\n    assert f(2) == 3\n    assert f(3) == 4\n"
)


def memory_graph(path: str = ":memory:") -> Any:
    """A compiled graph with its own checkpoint store (in memory unless a file path is given)."""
    return build_graph(checkpointer=SqliteSaver(sqlite3.connect(path, check_same_thread=False)))


def patch_llm(monkeypatch: pytest.MonkeyPatch, fn: Callable[..., Any]) -> None:
    for name in AGENT_MODULES:
        monkeypatch.setattr(f"codeforge.agents.{name}.resilient_invoke", fn)


def outage(monkeypatch: pytest.MonkeyPatch) -> None:
    def down(agent: str, messages: Any, schema: Any) -> Any:
        raise AllModelsDown("simulated outage")

    patch_llm(monkeypatch, down)


def scripted_fake(code: str, tests: str = GOOD_TESTS, fn_name: str = "f") -> FakeLLM:
    fake = FakeLLM()
    fake.set_response("router", RouteDecision(kind="python_function"))
    fake.set_response("requirements", Requirements(function_name=fn_name))
    fake.set_response("codegen", GeneratedCode(code=code))
    fake.set_response("testgen", GeneratedTests(tests=tests))
    return fake


def sandbox_spy(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    calls: list[int] = []
    real = graph_module.run_pytest_sandboxed

    def spy(*a: Any, **k: Any) -> Any:
        calls.append(1)
        return real(*a, **k)

    monkeypatch.setattr(graph_module, "run_pytest_sandboxed", spy)
    return calls


# ---------- cassettes (record once against a real provider, replay in CI) ----------

def _cassette_key(agent: str, messages: Any, schema: Any) -> str:
    blob = json.dumps({"agent": agent, "messages": messages, "schema": schema.__name__}, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()[:24]


def cassette_invoke(record: bool) -> Callable[..., Any]:
    """Return a resilient_invoke replacement that records (live) or replays (CI)."""
    from codeforge.llm import resilient_invoke as real_invoke

    def invoke(agent: str, messages: Any, schema: Any) -> Any:
        path = CASSETTE_DIR / f"{_cassette_key(agent, messages, schema)}.json"
        if record:
            result, tier = real_invoke(agent, messages, schema)
            CASSETTE_DIR.mkdir(exist_ok=True)
            path.write_text(
                json.dumps(
                    {"agent": agent, "schema": schema.__name__, "tier": tier, "response": result.model_dump(),
                     "source": "recorded-live"},
                    indent=2,
                ),
                encoding="utf-8",
            )
            return result, tier
        if not path.exists():
            pytest.skip("no cassette for this case; record with `pytest evals --record` and a real API key")
        data = json.loads(path.read_text(encoding="utf-8"))
        return schema(**data["response"]), data["tier"]

    return invoke
