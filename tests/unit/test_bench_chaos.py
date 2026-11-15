from codeforge.bench.chaos import (
    TEMPLATE_TASKS,
    UNKNOWN_TASKS,
    run_requests,
    tier_distribution,
)
from codeforge.bench.requests import CHAOS_REQUESTS


def test_chaos_20_of_20_answered_and_tier_reported():
    rows = run_requests(CHAOS_REQUESTS, rate=0.6, seed=42, offline=True)
    assert len(rows) == 20
    assert all(r.answered for r in rows), [r for r in rows if not r.answered]
    assert sum(r.injected_failures for r in rows) > 0  # chaos really happened
    assert set(tier_distribution(rows)) <= {"primary", "fallback", "template", "cache"}


def test_total_outage_templates_pass_unknown_refused_with_ticket():
    rows = run_requests(TEMPLATE_TASKS + UNKNOWN_TASKS, rate=0.0, seed=1, offline=True, outage=True)
    by_prompt = {r.prompt: r for r in rows}
    for p in TEMPLATE_TASKS:
        assert by_prompt[p].served_by == "template" and by_prompt[p].tests_passed
    for p in UNKNOWN_TASKS:
        assert by_prompt[p].served_by == "refused" and by_prompt[p].ticket
        assert not by_prompt[p].tests_passed  # never invented code
