"""Privacy and identity: PII masking, identity comes from config, users cannot read each other's runs."""
from typing import Any

import pytest

from codeforge.config import Identity, current_identity
from codeforge.entrypoint import submit
from codeforge.privacy.pii import mask_text
from codeforge.state import RunResult
from codeforge.store.db import init_db
from codeforge.store.runs import (
    RunNotFound,
    RunStore,
)
from evals.checks.common import FIXED, Env
from tests.helpers import (
    memory_graph,
    patch_llm,
    scripted_fake,
)


def check_identity_spoof(case: dict, env: Env) -> None:
    assert current_identity().role == "student"

def check_identity_spoof_graph(case: dict, env: Env) -> None:
    patch_llm(env.mp, scripted_fake(FIXED))
    res = submit(case["input"], graph=memory_graph())
    assert res.values["identity"].role == "student"
    assert res.values["identity"].user_id == "u_alice"

def check_cross_user_read(case: dict, env: Env) -> None:
    db = init_db("sqlite:///:memory:")
    alice = RunStore(db, lambda: Identity(user_id="u_alice", role="student"))
    bob = RunStore(db, lambda: Identity(user_id="u_bob", role="student"))
    alice.save_run("run_alice", "k1", "req", "code", "tests", RunResult(exit_code=0, passed=1))
    with pytest.raises(RunNotFound) as exc_missing:
        bob.get_run("run_alice")
    with pytest.raises(RunNotFound) as exc_unknown:
        bob.get_run("does-not-exist")
    assert str(exc_missing.value) == case["expect"] == str(exc_unknown.value)

def check_pii(case: dict, env: Env) -> None:
    assert case["expect"] in mask_text(case["input"])

def check_pii_negative(case: dict, env: Env) -> None:
    assert case["expect"] in mask_text(case["input"])

def check_checkpoint_no_pii(case: dict, env: Env) -> None:
    import sqlite3

    from langgraph.checkpoint.sqlite import SqliteSaver

    from codeforge.pipeline import build_graph

    patch_llm(env.mp, scripted_fake(FIXED))
    db = env.tmp / "ckpt_golden.db"
    conn = sqlite3.connect(str(db), check_same_thread=False)
    submit(case["input"], thread_id="gp", graph=build_graph(checkpointer=SqliteSaver(conn)))
    conn.commit()
    conn.close()
    raw = b"".join(p.read_bytes() for p in env.tmp.glob("ckpt_golden.db*"))
    assert b"sk-abcdefghijklmnop1234" not in raw and b"2345 6789 0124" not in raw and b"234567890124" not in raw


CHECKS: dict[str, Any] = {
    "identity_spoof": check_identity_spoof,
    "identity_spoof_graph": check_identity_spoof_graph,
    "cross_user_read": check_cross_user_read,
    "pii_secret": check_pii,
    "pii_email": check_pii,
    "pii_card": check_pii,
    "pii_aadhaar": check_pii,
    "pii_phone": check_pii,
    "pii_negative": check_pii_negative,
    "checkpoint_no_pii": check_checkpoint_no_pii,
}
