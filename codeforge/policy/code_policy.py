import ast
from dataclasses import dataclass, field

from codeforge.state import PolicyDecision

POLICY_VERSION = "2"

DENY_NAMES = {"__builtins__", "builtins", "__loader__", "__spec__"}

DENY_ATTR_CALLS = {
    "system", "popen", "spawn", "spawnl", "spawnv", "execv", "execve", "execl",
    "fork", "kill", "remove", "unlink", "rmtree", "rmdir", "removedirs", "chmod", "chown",
}

DENY_DUNDER_STRINGS = {
    "__import__", "__subclasses__", "__globals__", "__builtins__",
    "__code__", "__class__", "__bases__", "__mro__",
}

PRIVATE_DENY_MODULES = {
    "posix", "nt", "_socket", "_posixsubprocess", "resource", "gc",
    "inspect", "code", "codeop", "marshal",
}

DENY_IMPORTS = {
    "os",
    "sys",
    "subprocess",
    "shutil",
    "ctypes",
    "importlib",
    "multiprocessing",
    "signal",
    "pty",
    "builtins",
}

NEEDS_APPROVAL_IMPORTS = {
    "socket",
    "requests",
    "urllib",
    "http",
    "pathlib",
    "tempfile",
    "io",
    "sqlite3",
    "pickle",
}

ALLOW_IMPORTS = {
    "math",
    "re",
    "functools",
    "itertools",
    "collections",
    "typing",
    "dataclasses",
    "string",
    "statistics",
    "heapq",
    "bisect",
    "operator",
    "decimal",
    "fractions",
    "datetime",
    "random",
    "json",
    "pytest",
    "solution",
    "__future__",  # compiler directives only (e.g. `annotations`); LLMs emit it habitually
}

DENY_CALL_NAMES = {
    "eval",
    "exec",
    "compile",
    "__import__",
    "globals",
    "locals",
    "vars",
    "breakpoint",
}

# Calls whose attribute-name argument must be a visible string constant.
DYNAMIC_ATTRIBUTE_FUNCTIONS = {"getattr", "setattr", "delattr", "hasattr"}

ALLOWED_DUNDERS = {
    "__init__",
    "__name__",
    "__main__",
    "__eq__",
    "__lt__",
    "__repr__",
    "__str__",
    "__len__",
    "__iter__",
    "__next__",
}


def _is_denied_module(root: str) -> bool:
    return root.startswith("_") or root in PRIVATE_DENY_MODULES


def _is_dunder(name: str) -> bool:
    return name.startswith("__") and name.endswith("__")


def _is_string_constant(node: ast.AST) -> bool:
    return isinstance(node, ast.Constant) and isinstance(node.value, str)


@dataclass
class _Findings:
    """Reasons collected while walking the AST. Any `deny` wins over any `approval`."""

    deny: list[str] = field(default_factory=list)
    approval: list[str] = field(default_factory=list)


def _check_import(findings: _Findings, module: str) -> None:
    root = module.split(".")[0]
    if root in ALLOW_IMPORTS:
        return
    if root in DENY_IMPORTS or _is_denied_module(root):
        findings.deny.append(f"Denied import '{module}'")
    elif root in NEEDS_APPROVAL_IMPORTS:
        findings.approval.append(f"Import requires approval: '{module}'")
    else:
        findings.approval.append(f"Unknown import requires approval: '{module}'")


def _check_dynamic_attribute_call(findings: _Findings, node: ast.Call, fn_name: str) -> None:
    """getattr/setattr/delattr/hasattr: the attribute name must be a visible string constant."""
    if len(node.args) < 2 or not _is_string_constant(node.args[1]):
        findings.deny.append(f"Denied dynamic attribute access via {fn_name}()")

    first_arg = node.args[0] if node.args else None
    if isinstance(first_arg, ast.Name) and first_arg.id in {"__builtins__", "builtins"}:
        findings.deny.append(f"Denied access to builtins via {fn_name}()")

    for arg in node.args:
        if not _is_string_constant(arg):
            continue
        value = arg.value  # type: ignore[attr-defined]
        if value in DENY_CALL_NAMES:
            findings.deny.append(f"Denied {fn_name} for restricted function '{value}'")
        elif _is_dunder(value) and value not in ALLOWED_DUNDERS:
            findings.deny.append(f"Denied dynamic dunder access via {fn_name}('{value}')")


def _check_call(findings: _Findings, node: ast.Call) -> None:
    func = node.func
    if isinstance(func, ast.Name):
        name = func.id
        if name in DENY_CALL_NAMES:
            findings.deny.append(f"Denied call to '{name}()'")
        elif name == "open":
            findings.approval.append("Call to 'open()' requires approval")
        elif name == "type" and len(node.args) == 3:
            findings.approval.append("Dynamic class creation via type() requires approval")
        elif name in DYNAMIC_ATTRIBUTE_FUNCTIONS:
            _check_dynamic_attribute_call(findings, node, name)
    elif isinstance(func, ast.Attribute):
        method = func.attr
        if method in DENY_CALL_NAMES:
            findings.deny.append(f"Denied call to '{method}()'")
        elif method in DENY_ATTR_CALLS:
            findings.deny.append(f"Denied call to '.{method}()'")
        elif method == "open":
            findings.approval.append("Call to 'open()' requires approval")


def _check_node(findings: _Findings, node: ast.AST) -> None:
    if isinstance(node, ast.Import):
        for alias in node.names:
            _check_import(findings, alias.name)
    elif isinstance(node, ast.ImportFrom):
        if node.level > 0:
            findings.deny.append("Denied relative import")
        elif node.module:
            _check_import(findings, node.module)
    elif isinstance(node, ast.Call):
        _check_call(findings, node)
    elif isinstance(node, ast.Name):
        if node.id in DENY_NAMES:
            findings.deny.append(f"Denied reference to '{node.id}'")
    elif isinstance(node, ast.Constant):
        if isinstance(node.value, str) and any(d in node.value for d in DENY_DUNDER_STRINGS):
            findings.deny.append("Denied dunder string literal")
    elif isinstance(node, ast.Attribute) and _is_dunder(node.attr) and node.attr not in ALLOWED_DUNDERS:
        findings.deny.append(f"Denied dunder attribute access: '{node.attr}'")


def evaluate_code_policy(code: str) -> PolicyDecision:
    """Decide allow / needs_approval / deny for generated code by inspecting its AST.

    Pure function: the code is parsed, never executed. If anything is denied the verdict is
    deny; otherwise if anything needs a human the verdict is needs_approval; else allow.
    """
    if not code or not code.strip():
        return PolicyDecision(verdict="allow", reasons=[])

    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return PolicyDecision(verdict="deny", reasons=[f"Unparseable syntax: {e.msg}"])

    findings = _Findings()
    for node in ast.walk(tree):
        _check_node(findings, node)

    if findings.deny:
        return PolicyDecision(verdict="deny", reasons=findings.deny)
    if findings.approval:
        return PolicyDecision(verdict="needs_approval", reasons=findings.approval)
    return PolicyDecision(verdict="allow", reasons=[])
