"""Chaos benchmark: send requests while the model provider fails, and report what happened.

Everything in the report is computed from the real graph state; nothing is hardcoded.
"""
import os
import random
import tempfile
import time
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import patch

from codeforge import llm
from codeforge.bench.offline import SimulatedLLM
from codeforge.entrypoint import submit
from codeforge.pipeline import build_graph
from codeforge.runtime import reset_runtime_state

TEMPLATE_TASKS = [
    "Write an is_palindrome function",
    "Calculate the factorial of a number",
    "Generate the nth fibonacci number",
    "Implement fizzbuzz for numbers 1 to n",
    "Calculate the sum of a list of numbers",
]
UNKNOWN_TASKS = [
    "Implement an advanced ray tracing BVH algorithm",
    "Write a lock-free concurrent skip list",
]
ANSWER_TIERS = {"primary", "fallback", "template", "cache"}


@dataclass
class RequestRow:
    idx: int
    prompt: str
    served_by: str
    injected_failures: int
    tests_passed: bool
    ticket: str
    answered: bool
    breakers: dict[str, str] = field(default_factory=dict)


@contextmanager
def isolated_run(rate: float, outage: bool, seed: int, offline: bool, fast: bool) -> Iterator[dict[str, int]]:
    """Run with a private DB, the given chaos settings and (optionally) the simulated provider.

    Yields a counter dict whose "injected" entry counts synthetic failures. Everything patched
    here (environment, llm functions) is restored on exit.
    """
    counter = {"injected": 0}
    real_maybe_fail = llm.maybe_fail

    def counting_maybe_fail(*args: Any, **kwargs: Any) -> None:
        try:
            real_maybe_fail(*args, **kwargs)
        except Exception:
            counter["injected"] += 1
            raise

    with tempfile.TemporaryDirectory(prefix="cf_bench_") as tmp, ExitStack() as stack:
        # ExitStack unwinds last-in-first-out: registered first, so this reset runs LAST,
        # after the environment and the patches below have been restored.
        stack.callback(reset_runtime_state)
        stack.enter_context(
            patch.dict(
                os.environ,
                {
                    "CODEFORGE_DB_URL": f"sqlite:///{tmp}/bench.db",
                    "CODEFORGE_CHECKPOINT_PATH": f"{tmp}/ckpt.db",
                    "CHAOS_FAILURE_RATE": str(rate),
                    "TOTAL_OUTAGE": "true" if outage else "false",
                },
            )
        )
        reset_runtime_state()  # pick up the new environment (before llm functions are patched)
        stack.enter_context(patch.object(llm, "maybe_fail", counting_maybe_fail))
        if offline:
            stack.enter_context(patch.object(llm, "get_llm_for_tier", lambda tier: SimulatedLLM()))
        if fast:
            stack.enter_context(patch.object(llm.time, "sleep", lambda seconds: None))
        random.seed(seed)
        yield counter


def is_answered(values: dict[str, Any]) -> bool:
    """Answered = code+tests delivered with a known serving tier, OR an honest refusal with a ticket."""
    tier = values.get("served_by", "")
    if tier in ANSWER_TIERS:
        return bool(values.get("code")) and bool(values.get("tests")) and values.get("run_result") is not None
    if tier == "refused":
        return values.get("ticket") is not None  # honest refusal = refused tier + handoff ticket
    return False


def run_requests(
    prompts: list[str], rate: float, seed: int, offline: bool, outage: bool = False, fast: bool = True
) -> list[RequestRow]:
    rows: list[RequestRow] = []
    with isolated_run(rate, outage, seed, offline, fast) as counter:
        graph = build_graph()
        for i, prompt in enumerate(prompts, start=1):
            before = counter["injected"]
            vals = submit(prompt, thread_id=f"chaos_{i}_{int(time.time())}", graph=graph).values
            res = vals.get("run_result")
            ticket = vals.get("ticket")
            rows.append(
                RequestRow(
                    idx=i,
                    prompt=prompt,
                    served_by=vals.get("served_by", "?"),
                    injected_failures=counter["injected"] - before,
                    tests_passed=bool(res and res.exit_code == 0 and not res.timed_out),
                    ticket=ticket.ticket_id if ticket else "",
                    answered=is_answered(vals),
                    breakers=llm.breaker_states(),
                )
            )
    return rows


def tier_distribution(rows: list[RequestRow]) -> dict[str, int]:
    dist: dict[str, int] = {}
    for r in rows:
        dist[r.served_by] = dist.get(r.served_by, 0) + 1
    return dist


def render_report(chaos: list[RequestRow], outage: list[RequestRow], meta: dict[str, Any]) -> str:
    answered = sum(r.answered for r in chaos)
    lines = [
        "# Chaos Run Log (R5)",
        "",
        f"- **Mode**: {meta['mode']}",
        f"- **Failure rate**: {meta['rate']:.0%} synthetic 429/503/timeouts | **Seed**: {meta['seed']}",
        f"- **Injected failures (total)**: {sum(r.injected_failures for r in chaos)}",
        "",
        "## Phase 1: chaos on every model call",
        "",
        "| # | Prompt | Served tier | Injected failures | Tests passed | Ticket | Answered |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in chaos:
        lines.append(
            f"| {r.idx} | {r.prompt} | `{r.served_by}` | {r.injected_failures} | "
            f"{'✅' if r.tests_passed else '—'} | {r.ticket or '—'} | {'✅' if r.answered else '❌'} |"
        )
    dist = ", ".join(f"{k}={v}" for k, v in sorted(tier_distribution(chaos).items()))
    lines += [
        "",
        f"**Result: {answered}/{len(chaos)} requests answered** (tier distribution: {dist}).",
        f"Final breaker states: {chaos[-1].breakers if chaos else {}}",
        "",
        "## Phase 2: total outage (TOTAL_OUTAGE=true)",
        "",
        "| # | Prompt | Served tier | Tests passed | Ticket | Answered |",
        "|---|---|---|---|---|---|",
    ]
    for r in outage:
        lines.append(
            f"| {r.idx} | {r.prompt} | `{r.served_by}` | {'✅' if r.tests_passed else '—'} | "
            f"{r.ticket or '—'} | {'✅' if r.answered else '❌'} |"
        )
    tpl_ok = all(r.tests_passed for r in outage if r.prompt in TEMPLATE_TASKS)
    unk_ok = all(r.served_by == "refused" and r.ticket for r in outage if r.prompt in UNKNOWN_TASKS)
    lines += [
        "",
        f"- Template tasks return code + passing tests during outage: **{'YES' if tpl_ok else 'NO'}**",
        f"- Unknown tasks get an honest refusal + ticket (never invented code): **{'YES' if unk_ok else 'NO'}**",
    ]
    if meta["mode"].startswith("SIMULATED"):
        lines += [
            "",
            (
                "> ⚠️ SIMULATED PROVIDER: no API key was set, so a deterministic stand-in model answered "
                "template-matching tasks. The chaos injection, retries, breakers and fallback are the real "
                "CodeForge code paths; model quality is not measured here."
            ),
        ]
    return "\n".join(lines) + "\n"
