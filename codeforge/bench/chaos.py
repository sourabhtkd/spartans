"""Chaos benchmark: send requests while the model provider fails, and report what happened.

Everything in the report is computed from the real graph state; nothing is hardcoded.
"""
import os
import random
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from codeforge import llm
from codeforge.bench.offline import install_simulated_provider
from codeforge.config import clear_settings_cache
from codeforge.entrypoint import submit
from codeforge.graph import build_graph

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
    served_tier: str
    injected_failures: int
    tests_passed: bool
    ticket: str
    answered: bool
    breakers: dict[str, str] = field(default_factory=dict)


@contextmanager
def isolated_run(rate: float, outage: bool, seed: int, offline: bool, fast: bool) -> Iterator[dict[str, int]]:
    """Isolated DB + env + (optionally) simulated provider; restores everything on exit."""
    keys = ["CODEFORGE_DB_URL", "CODEFORGE_CHECKPOINT_PATH", "CHAOS_FAILURE_RATE", "TOTAL_OUTAGE"]
    saved = {k: os.environ.get(k) for k in keys}
    real_maybe_fail, real_sleep, real_factory = llm.maybe_fail, llm.time.sleep, llm.get_llm_for_tier
    counter = {"injected": 0}
    with tempfile.TemporaryDirectory(prefix="cf_bench_") as tmp:
        os.environ["CODEFORGE_DB_URL"] = f"sqlite:///{tmp}/bench.db"
        os.environ["CODEFORGE_CHECKPOINT_PATH"] = f"{tmp}/ckpt.db"
        os.environ["CHAOS_FAILURE_RATE"] = str(rate)
        os.environ["TOTAL_OUTAGE"] = "true" if outage else "false"
        clear_settings_cache()
        llm.reset_breakers()
        llm.clear_llm_cache()
        random.seed(seed)

        def counting_maybe_fail(*a: Any, **k: Any) -> None:
            try:
                real_maybe_fail(*a, **k)
            except Exception:
                counter["injected"] += 1
                raise

        llm.maybe_fail = counting_maybe_fail  # type: ignore[assignment]
        if offline:
            install_simulated_provider(llm)
        if fast:
            llm.time.sleep = lambda s: None  # type: ignore[assignment]
        try:
            yield counter
        finally:
            llm.maybe_fail = real_maybe_fail  # type: ignore[assignment]
            llm.time.sleep = real_sleep
            llm.get_llm_for_tier = real_factory  # type: ignore[assignment]
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
            clear_settings_cache()
            llm.reset_breakers()
            llm.clear_llm_cache()
            import codeforge.cost.semantic_cache as sc

            sc._semantic_cache = None


def is_answered(values: dict[str, Any]) -> bool:
    """Answered = code+tests delivered with a known serving tier, OR an honest refusal with a ticket."""
    tier = values.get("served_tier", "")
    if tier in ANSWER_TIERS:
        return bool(values.get("code")) and bool(values.get("tests")) and values.get("run_result") is not None
    if tier == "refused":
        return values.get("ticket") is not None and "cannot generate code right now" in values.get("report", "")
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
                    served_tier=vals.get("served_tier", "?"),
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
        dist[r.served_tier] = dist.get(r.served_tier, 0) + 1
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
            f"| {r.idx} | {r.prompt} | `{r.served_tier}` | {r.injected_failures} | "
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
            f"| {r.idx} | {r.prompt} | `{r.served_tier}` | {'✅' if r.tests_passed else '—'} | "
            f"{r.ticket or '—'} | {'✅' if r.answered else '❌'} |"
        )
    tpl_ok = all(r.tests_passed for r in outage if r.prompt in TEMPLATE_TASKS)
    unk_ok = all(r.served_tier == "refused" and r.ticket for r in outage if r.prompt in UNKNOWN_TASKS)
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
