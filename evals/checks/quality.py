"""Quality: routing, requirements and correctness against hidden reference tests (incl. model-backed cases)."""
from typing import Any

from codeforge.agents.requirements import RequirementsAgent
from codeforge.agents.router import RouterAgent
from codeforge.entrypoint import submit
from evals.checks.common import Env, run_reference
from tests.helpers import (
    cassette_invoke,
    memory_graph,
    outage,
    patch_llm,
)


def check_routing_fallback(case: dict, env: Env) -> None:
    outage(env.mp)
    route = RouterAgent()({"request_masked": case["input"]})["route"]
    assert route.kind == case["expect"]

def check_requirements_fallback(case: dict, env: Env) -> None:
    outage(env.mp)
    reqs = RequirementsAgent()({"request_masked": case["input"]})["requirements"]
    assert reqs.function_name == case["expect"]

def check_correctness_template(case: dict, env: Env) -> None:
    outage(env.mp)  # deterministic: the template tier answers
    res = submit(case["input"], graph=memory_graph())
    assert res.values.get("code"), res.values.get("report")
    ref = run_reference(res.values["code"], case["expect"])
    assert ref.exit_code == 0, ref.stdout

def check_llm_routing(case: dict, env: Env) -> None:
    env.mp.setattr("codeforge.agents.router.resilient_invoke", cassette_invoke(env.record))
    assert RouterAgent()({"request_masked": case["input"]})["route"].kind == case["expect"]

def check_llm_requirements(case: dict, env: Env) -> None:
    env.mp.setattr("codeforge.agents.requirements.resilient_invoke", cassette_invoke(env.record))
    reqs = RequirementsAgent()({"request_masked": case["input"]})["requirements"]
    assert case["expect"] in reqs.function_name.lower() and reqs.edge_cases

def check_llm_correctness(case: dict, env: Env) -> None:
    patch_llm(env.mp, cassette_invoke(env.record))
    res = submit(case["input"], graph=memory_graph())
    assert res.values.get("code"), res.values.get("report")
    assert run_reference(res.values["code"], case["expect"]).exit_code == 0


CHECKS: dict[str, Any] = {
    "routing_fallback": check_routing_fallback,
    "requirements_fallback": check_requirements_fallback,
    "correctness_template": check_correctness_template,
    "llm_routing": check_llm_routing,
    "llm_requirements": check_llm_requirements,
    "llm_correctness": check_llm_correctness,
}
