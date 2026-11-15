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


class TestValidator:
    """Validates generated tests before policy gate."""

    __test__ = False

    def __call__(self, state: CodeForgeState) -> dict[str, Any]:
        tests = state.get("tests", "")
        reqs = state.get("requirements")
        target_fn = reqs.function_name if reqs else None
        is_valid, reason = validate_tests(tests, target_fn)
        events = list(state.get("events", []))
        events.append({
            "stage": "test_validator",
            "is_valid": is_valid,
            "reason": reason,
        })
        return {"events": events}
