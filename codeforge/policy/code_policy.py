import ast

from codeforge.state import PolicyDecision

DENY_IMPORTS = {
    
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


def evaluate_code_policy(code: str) -> PolicyDecision:
    """Evaluates code safety purely through AST inspection.
    Never executes code.
    Returns PolicyDecision with verdict 'allow', 'needs_approval', or 'deny'.
    """
    if not code or not code.strip():
        return PolicyDecision(verdict="allow", reasons=[])

    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return PolicyDecision(verdict="deny", reasons=[f"Unparseable syntax: {e.msg}"])

    deny_reasons: list[str] = []
    approval_reasons: list[str] = []

    for node in ast.walk(tree):
        # 1. Imports
        if isinstance(node, ast.Import):
            for alias in node.names:
                mod_root = alias.name.split(".")[0]
                if mod_root in DENY_IMPORTS:
                    deny_reasons.append(f"Denied import '{alias.name}'")
                elif mod_root in NEEDS_APPROVAL_IMPORTS:
                    approval_reasons.append(f"Import requires approval: '{alias.name}'")
                elif mod_root not in ALLOW_IMPORTS:
                    approval_reasons.append(f"Unknown import requires approval: '{alias.name}'")

        elif isinstance(node, ast.ImportFrom):
            if node.module:
                mod_root = node.module.split(".")[0]
                if mod_root in DENY_IMPORTS:
                    deny_reasons.append(f"Denied import '{node.module}'")
                elif mod_root in NEEDS_APPROVAL_IMPORTS:
                    approval_reasons.append(f"Import requires approval: '{node.module}'")
                elif mod_root not in ALLOW_IMPORTS:
                    approval_reasons.append(f"Unknown import requires approval: '{node.module}'")

        # 2. Function calls
        elif isinstance(node, ast.Call):
            # Direct calls by name
            if isinstance(node.func, ast.Name):
                fn_name = node.func.id
                if fn_name in DENY_CALL_NAMES:
                    deny_reasons.append(f"Denied call to '{fn_name}()'")
                elif fn_name == "open":
                    approval_reasons.append("Call to 'open()' requires approval")
                elif fn_name in {"getattr", "setattr", "delattr"}:
                    for arg in node.args:
                        if (
                            isinstance(arg, ast.Constant)
                            and isinstance(arg.value, str)
                            and arg.value.startswith("__")
                            and arg.value.endswith("__")
                        ):
                            deny_reasons.append(
                                f"Denied dynamic dunder access via {fn_name}('{arg.value}')"
                            )
            elif isinstance(node.func, ast.Attribute):
                attr_name = node.func.attr
                if attr_name in DENY_CALL_NAMES:
                    deny_reasons.append(f"Denied call to '{attr_name}()'")
                elif attr_name == "open":
                    approval_reasons.append("Call to 'open()' requires approval")

        # 3. Attribute access (dunder escapes)
        elif isinstance(node, ast.Attribute):
            attr = node.attr
            if attr.startswith("__") and attr.endswith("__") and attr not in ALLOWED_DUNDERS:
                deny_reasons.append(f"Denied dunder attribute access: '{attr}'")

    if deny_reasons:
        return PolicyDecision(verdict="deny", reasons=deny_reasons)
    if approval_reasons:
        return PolicyDecision(verdict="needs_approval", reasons=approval_reasons)
    return PolicyDecision(verdict="allow", reasons=[])
