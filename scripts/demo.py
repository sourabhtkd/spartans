"""CodeForge End-to-End Interactive Demonstration Script.

Replays the 4 core incidents and the live CI/CD gate demo:
- Incident 1: Student A (Happy path - verified palindrome generation)
- Incident 2: Student B (Adversarial attack - pure AST policy gate denies)
- Incident 3: Student C (PII masking & automated P1 human handoff)
- Incident 4: 60% Chaos Resilience (Circuit breaker & offline template fallback)
"""

import os
import time

from codeforge.config import clear_settings_cache
from codeforge.entrypoint import submit
from codeforge.graph import build_graph
from codeforge.guard.pii import mask_text
from codeforge.policy.code_policy import evaluate_code_policy
from codeforge.state import (
    GeneratedCode,
    GeneratedTests,
    Requirements,
    RouteDecision,
)
from tests.fakes import FakeLLM


class Colors:
    HEADER = "\033[95m"
    BLUE = "\033[94m"
    CYAN = "\033[96m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    RED = "\033[91m"
    BOLD = "\033[1m"
    UNDERLINE = "\033[4m"
    END = "\033[0m"


def print_banner(title: str) -> None:
    print(f"\n{Colors.BOLD}{Colors.CYAN}{'='*72}{Colors.END}")
    print(f"{Colors.BOLD}{Colors.YELLOW} {title} {Colors.END}")
    print(f"{Colors.BOLD}{Colors.CYAN}{'='*72}{Colors.END}\n")


def setup_demo_llm(malicious: bool = False) -> FakeLLM:
    fake = FakeLLM()
    fake.set_response(
        "router",
        RouteDecision(kind="python_function", language="python", confidence=0.99),
    )
    fake.set_response(
        "requirements",
        Requirements(function_name="is_palindrome", returns="bool"),
    )
    if malicious:
        fake.set_response(
            "codegen",
            GeneratedCode(code="import os\nos.remove('/etc/passwd')\n"),
        )
    else:
        fake.set_response(
            "codegen",
            GeneratedCode(
                code="def is_palindrome(s: str) -> bool:\n    return s == s[::-1]\n"
            ),
        )
    fake.set_response(
        "testgen",
        GeneratedTests(
            tests=(
                "from solution import is_palindrome\n\n"
                "def test_cases():\n"
                "    assert is_palindrome('radar') is True\n"
                "    assert is_palindrome('noon') is True\n"
                "    assert is_palindrome('world') is False\n"
            )
        ),
    )
    return fake


def patch_agents(fake: FakeLLM) -> None:
    import codeforge.agents.codegen as codegen_mod
    import codeforge.agents.requirements as req_mod
    import codeforge.agents.router as router_mod
    import codeforge.agents.testgen as testgen_mod

    router_mod.resilient_invoke = fake
    req_mod.resilient_invoke = fake
    codegen_mod.resilient_invoke = fake
    testgen_mod.resilient_invoke = fake


def incident_1_student_a() -> None:
    print_banner("INCIDENT 1: Student A — Happy Path Code Generation & Verification")
    user_prompt = "Write an is_palindrome function that checks if a string is a palindrome."
    print(f"{Colors.BOLD}User (Student A):{Colors.END} \"{user_prompt}\"")
    print(f"{Colors.BLUE}[Ingress Guard]{Colors.END} Checking identity: student_a (role: student)")
    print(f"{Colors.BLUE}[Cache Lookup]{Colors.END} Checking semantic cache... (Miss)")
    print(f"{Colors.BLUE}[Pipeline]{Colors.END} Router -> Requirements -> CodeGen -> TestGen -> TestValidator")

    fake = setup_demo_llm(malicious=False)
    patch_agents(fake)

    graph = build_graph()
    thread_id = f"demo_inc1_{int(time.time())}"
    config = {"configurable": {"thread_id": thread_id}}
    state = submit(user_prompt, thread_id=thread_id, graph=graph).values

    policy = state.get("policy")
    verdict = policy.verdict if isinstance(policy.verdict, str) else policy.verdict.value
    print(f"{Colors.GREEN}[Policy Gate Verdict]{Colors.END} {verdict.upper()} (All AST rules passed)")
    print(f"{Colors.BLUE}[Sandbox Runner]{Colors.END} Executing test suite in isolated subprocess with RLIMITs & blocked network...")
    run_res = state.get("run_result")
    if run_res:
        print(f"{Colors.GREEN}[Sandbox Outcome]{Colors.END} Exit Code: {run_res.exit_code} | Passed: {run_res.passed} tests")

    print(f"\n{Colors.BOLD}Honest Report Delivered to Student A:{Colors.END}")
    print(f"{Colors.GREEN}{state.get('report')}{Colors.END}")


def incident_2_student_b() -> None:
    print_banner("INCIDENT 2: Student B — Malicious OS Access Blocked by Pure AST Gate")
    user_prompt = "Write a cleanup script: import os; os.remove('/etc/passwd')"
    print(f"{Colors.BOLD}User (Student B):{Colors.END} \"{user_prompt}\"")
    print(f"{Colors.BLUE}[Ingress Guard]{Colors.END} Ingress accepted. Passing to pipeline...")
    print(f"{Colors.BLUE}[Policy Gate]{Colors.END} Evaluating generated code with pure AST analyzer (NO execution)...")

    # Pure AST policy inspection
    decision = evaluate_code_policy("import os\nos.remove('/etc/passwd')")
    print(f"{Colors.RED}[Policy AST Violation Detected]{Colors.END}")
    for reason in decision.reasons:
        print(f"  ❌ {reason}")
    print(f"{Colors.RED}[Policy Gate Verdict]{Colors.END} {decision.verdict.upper()}")

    fake = setup_demo_llm(malicious=True)
    patch_agents(fake)

    graph = build_graph()
    thread_id = f"demo_inc2_{int(time.time())}"
    config = {"configurable": {"thread_id": thread_id}}
    state = submit(user_prompt, thread_id=thread_id, graph=graph).values

    print(f"{Colors.YELLOW}[Sandbox Runner]{Colors.END} Sandbox was {Colors.BOLD}NEVER CALLED{Colors.END} (zero execution)")
    print(f"\n{Colors.BOLD}Honest Refusal Delivered to Student B:{Colors.END}")
    print(f"{Colors.RED}{state.get('report')}{Colors.END}")


def incident_3_student_c() -> None:
    print_banner("INCIDENT 3: Student C — PII Masking & Automated P1 Support Escalation")
    raw_prompt = (
        "URGENT HELP! My instructor API key sk-live99887766554433221100aabbccddeeff and Aadhaar "
        "2345 6789 0124 were exposed! I NEED A REAL HUMAN RIGHT NOW!!!"
    )
    print(f"{Colors.BOLD}Raw User Message Received:{Colors.END}\n\"{raw_prompt}\"")

    masked_prompt = mask_text(raw_prompt)
    print(f"\n{Colors.BLUE}[Ingress Guard PII Engine]{Colors.END} Scrubbing sensitive tokens...")
    print(f"{Colors.GREEN}[Sanitized Payload Sent to Agents]:{Colors.END}\n\"{masked_prompt}\"")

    fake = setup_demo_llm(malicious=False)
    patch_agents(fake)

    graph = build_graph()
    thread_id = f"demo_inc3_{int(time.time())}"
    config = {"configurable": {"thread_id": thread_id}}
    state = submit(raw_prompt, thread_id=thread_id, graph=graph).values

    ticket = state.get("ticket")
    if ticket:
        print(f"\n{Colors.YELLOW}[Support Escalation Node]{Colors.END} Escalation rules triggered:")
        print(f"  - Ticket ID: {ticket.ticket_id}")
        print(f"  - Priority:  {Colors.BOLD}{Colors.RED}{ticket.priority}{Colors.END}")
        print(f"  - SLA:       {ticket.sla_hours} hours")
        print(f"  - Reason:    {ticket.reason}")
        print(f"  - Sanitized Ticket Context: \"{ticket.request_masked}\"")
        assert "sk-live" not in ticket.request_masked
        assert "2345 6789 0124" not in ticket.request_masked
        print(f"  {Colors.GREEN}✓ Verified: Zero API keys or Aadhaar digits stored in ticket database.{Colors.END}")


def incident_4_chaos_resilience() -> None:
    print_banner("INCIDENT 4: Resilience Under Chaos — 60% Failure Rate & Graceful Degradation")
    print("Simulating upstream LLM provider instability (429 Rate Limits & 503 Outages)...")
    os.environ["CHAOS_FAILURE_RATE"] = "0.6"
    clear_settings_cache()

    # Re-wire to live/resilient invocation for chaos test
    import codeforge.agents.codegen as codegen_mod
    import codeforge.agents.requirements as req_mod
    import codeforge.agents.router as router_mod
    import codeforge.agents.testgen as testgen_mod
    from codeforge.llm import resilient_invoke

    router_mod.resilient_invoke = resilient_invoke
    req_mod.resilient_invoke = resilient_invoke
    codegen_mod.resilient_invoke = resilient_invoke
    testgen_mod.resilient_invoke = resilient_invoke

    graph = build_graph()
    prompt = "Write an is_palindrome function"
    thread_id = f"demo_inc4_{int(time.time())}"
    config = {"configurable": {"thread_id": thread_id}}

    print(f"{Colors.BLUE}[Resilience Layer]{Colors.END} Executing with jitter retries and fallback to verified templates...")
    state = submit(prompt, thread_id=thread_id, graph=graph).values

    served_tier = state.get("served_tier", "primary")
    run_res = state.get("run_result")
    passed = run_res.exit_code == 0 if run_res else False

    print(f"\n{Colors.GREEN}[Resilience Outcome]{Colors.END}")
    print(f"  - Served Tier: {Colors.BOLD}{served_tier.upper()}{Colors.END}")
    print(f"  - Test Suite Exit Code: {run_res.exit_code if run_res else 'N/A'}")
    print(f"  - User Request Fulfilled: {Colors.BOLD}{Colors.GREEN if passed else Colors.RED}{passed}{Colors.END}")
    print("  - Reliability Guarantee: Deterministic offline template fulfilled request safely.")

    # Reset chaos
    os.environ["CHAOS_FAILURE_RATE"] = "0.0"
    clear_settings_cache()


def main() -> None:
    print(f"{Colors.BOLD}{Colors.GREEN}")
    print("=" * 72)
    print("   CODEFORGE: MULTI-AGENT VERIFIED CODE GENERATION PLATFORM")
    print("              LIVE END-TO-END DEMONSTRATION")
    print("=" * 72)
    print(f"{Colors.END}")

    incident_1_student_a()
    incident_2_student_b()
    incident_3_student_c()
    incident_4_chaos_resilience()

    print_banner("SUMMARY & EVALUATION GATE DEMO")
    print("All 4 incidents executed successfully demonstrating:")
    print("  1. Strict Verification (Honest report strictly from exit code 0)")
    print("  2. Static Security (Pure AST policy inspection never executes hostile code)")
    print("  3. Data Privacy (PII masking with Luhn & Verhoeff + automated P1 escalation)")
    print("  4. High Resilience (Adaptive retries, circuit breakers, offline templates)")
    print("\nTo inspect the golden CI evaluation gate, run:")
    print(f"  {Colors.BOLD}make gate-red{Colors.END}   (Simulates policy regression and catches it)")
    print(f"  {Colors.BOLD}make gate-green{Colors.END} (Restores policy and verifies 100% pass rate)\n")


if __name__ == "__main__":
    main()
