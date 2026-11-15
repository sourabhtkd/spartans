import ast
from typing import Any

from codeforge.state import CodeForgeState


def validate_tests(tests_code: str, target_fn: str | None = None) -> tuple[bool, str]:
    """Validates generated tests:
    - Syntax is valid (ast.parse)
    - Imports target function or solution module
    - Defines at least 1 test function (starts with 'test_')
    - Contains at least 3 assert statements
    """
    if not tests_code or not tests_code.strip():
        return False, "Empty test code"

    try:
        tree = ast.parse(tests_code)
    except SyntaxError as e:
        return False, f"SyntaxError in tests: {e.msg}"

    # Check for imports
    has_target_import = False
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (
            node.module == "solution"
            or (target_fn and any(alias.name == target_fn for alias in node.names))
        ) or isinstance(node, ast.Import) and (
            any(alias.name == "solution" for alias in node.names)
            or (target_fn and any(alias.name == target_fn for alias in node.names))
        ):
            has_target_import = True

    if target_fn and not has_target_import:
        # Fallback check: text-based if AST aliases are dynamic
        if f"from solution import {target_fn}" in tests_code or "import solution" in tests_code:
            has_target_import = True
        else:
            return False, f"Tests do not import target function '{target_fn}' from solution"

    # Check for test functions
    test_funcs = [
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name.startswith("test_")
    ]
    if not test_funcs:
        return False, "No test functions (def test_...) defined"

    # Check assert statements
    assert_count = sum(1 for node in ast.walk(tree) if isinstance(node, ast.Assert))
    if assert_count < 3:
        return False, f"Found only {assert_count} assert statements; at least 3 required"

    return True, "valid"


def _reference_check(tests_code: str, request_text: str, fn_name: str | None) -> tuple[bool, str]:
    """Run the generated tests against a known-good template implementation, when one exists
    and defines every function the tests import. Returns (agrees, reason)."""
    from codeforge.resilience.templates import match_template
    from codeforge.sandbox.runner import run_pytest_sandboxed

    tpl = match_template(f"{fn_name or ''} {request_text}")
    if tpl is None:
        return True, "no reference implementation"
    try:
        defined = {n.name for n in ast.parse(tpl.code).body if isinstance(n, ast.FunctionDef)}
        imported: set[str] = set()
        for node in ast.walk(ast.parse(tests_code)):
            if isinstance(node, ast.ImportFrom) and node.module == "solution":
                imported |= {a.name for a in node.names}
    except SyntaxError:
        return True, "unparseable"
    if not imported or not imported <= defined:
        return True, "reference does not define the tested functions"
    res = run_pytest_sandboxed(tpl.code, tests_code, timeout_s=10)
    if res.exit_code == 0:
        return True, "agrees with reference implementation"
    return False, "generated tests fail against the known-good reference implementation"


class TestValidator:
    """Validates generated tests before the policy gate.

    Sets ``tests_valid``; the graph routes invalid tests back to testgen once, then escalates.
    Once valid, ``tests_frozen`` is set so repair loops never rewrite the tests.
    """

    __test__ = False

    def __call__(self, state: CodeForgeState) -> dict[str, Any]:
        tests = state.get("tests", "")
        reqs = state.get("requirements")
        target_fn = reqs.function_name if reqs else None
        is_valid, reason = validate_tests(tests, target_fn)
        regen = state.get("test_regen_count", 0)
        if is_valid and state.get("served_by") not in ("template", "refused", "cache"):
            agrees, ref_reason = _reference_check(tests, state.get("request_masked", ""), target_fn)
            if not agrees:
                if regen < 1:
                    is_valid, reason = False, ref_reason
                else:
                    reason = f"accepted with warning: {ref_reason}"
            else:
                reason = f"valid ({ref_reason})"
        events = list(state.get("events", []))
        events.append({"stage": "test_validator", "is_valid": is_valid, "reason": reason})
        out: dict[str, Any] = {"events": events, "tests_valid": is_valid}
        if is_valid:
            out["tests_frozen"] = True
        else:
            out["test_regen_count"] = regen + 1
            out["escalate_reason"] = f"Invalid generated tests: {reason}"
        return out
