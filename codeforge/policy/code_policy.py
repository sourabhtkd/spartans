import ast

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


def _is_denied_module(root: str) -> bool:
    return root.startswith("_") or root in PRIVATE_DENY_MODULES


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
                if mod_root in DENY_IMPORTS or _is_denied_module(mod_root):
                    deny_reasons.append(f"Denied import '{alias.name}'")
                elif mod_root in NEEDS_APPROVAL_IMPORTS:
                    approval_reasons.append(f"Import requires approval: '{alias.name}'")
                elif mod_root not in ALLOW_IMPORTS:
                    approval_reasons.append(f"Unknown import requires approval: '{alias.name}'")

        elif isinstance(node, ast.ImportFrom):
            if node.level and node.level > 0:
                deny_reasons.append("Denied relative import")
            elif node.module:
                mod_root = node.module.split(".")[0]
                if mod_root in DENY_IMPORTS or _is_denied_module(mod_root):
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
                elif fn_name == "type" and len(node.args) == 3:
                    approval_reasons.append("Dynamic class creation via type() requires approval")
                elif fn_name in {"getattr", "setattr", "delattr", "hasattr"}:
                    if len(node.args) < 2 or not (
                        isinstance(node.args[1], ast.Constant) and isinstance(node.args[1].value, str)
                    ):
                        deny_reasons.append(f"Denied dynamic attribute access via {fn_name}()")
                    if node.args and isinstance(node.args[0], ast.Name) and node.args[0].id in {"__builtins__", "builtins"}:
                        deny_reasons.append(f"Denied access to builtins via {fn_name}()")
                    for arg in node.args:
                        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                            val = arg.value
                            if val in DENY_CALL_NAMES:
                                deny_reasons.append(f"Denied {fn_name} for restricted function '{val}'")
                            elif val.startswith("__") and val.endswith("__") and val not in ALLOWED_DUNDERS:
                                deny_reasons.append(
                                    f"Denied dynamic dunder access via {fn_name}('{val}')"
                                )
            elif isinstance(node.func, ast.Attribute):
                attr_name = node.func.attr
                if attr_name in DENY_CALL_NAMES:
                    deny_reasons.append(f"Denied call to '{attr_name}()'")
                elif attr_name in DENY_ATTR_CALLS:
                    deny_reasons.append(f"Denied call to '.{attr_name}()'")
                elif attr_name == "open":
                    approval_reasons.append("Call to 'open()' requires approval")

        # 3. Attribute access (dunder escapes)
        elif isinstance(node, ast.Name):
            if node.id in DENY_NAMES:
                deny_reasons.append(f"Denied reference to '{node.id}'")

        elif isinstance(node, ast.Constant):
            if isinstance(node.value, str) and any(d in node.value for d in DENY_DUNDER_STRINGS):
                deny_reasons.append("Denied dunder string literal")

        elif isinstance(node, ast.Attribute):
            attr = node.attr
            if attr.startswith("__") and attr.endswith("__") and attr not in ALLOWED_DUNDERS:
                deny_reasons.append(f"Denied dunder attribute access: '{attr}'")

    if deny_reasons:
        return PolicyDecision(verdict="deny", reasons=deny_reasons)
    if approval_reasons:
        return PolicyDecision(verdict="needs_approval", reasons=approval_reasons)
    return PolicyDecision(verdict="allow", reasons=[])
