import pytest

from codeforge.policy.code_policy import POLICY_VERSION, evaluate_code_policy

DENY_CASES = [
    '__builtins__["__import__"]("os")',
    '__builtins__["__imp"+"ort__"]("o"+"s").system("x")',
    "x = __builtins__",
    'getattr(str, "__cl"+"ass__")',
    "getattr(obj, name)",
    "from . import os",
    "from .. import x",
    "something.system('ls')",
    "something.popen('ls')",
    "p.rmtree('x')",
    "p.unlink()",
    "p.remove('x')",
    "p.rmdir()",
    "p.fork()",
    "p.kill(1, 9)",
    "import _socket",
    "import _posixsubprocess",
    "import posix",
    "import nt",
    "import resource",
    "import gc",
    "import inspect",
    "import marshal",
    "x = '__import__'",
    "x = '__subclasses__'",
]


@pytest.mark.parametrize("code", DENY_CASES)
def test_bypass_denied(code):
    assert evaluate_code_policy(code).verdict == "deny", code


def test_metaclass_trick_needs_approval():
    assert evaluate_code_policy("X = type('X', (object,), {})").verdict == "needs_approval"


def test_student_b_still_denied_and_palindrome_allowed():
    assert evaluate_code_policy("import os\nos.remove('x')").verdict == "deny"
    ok = "def is_palindrome(s):\n    t = ''.join(c.lower() for c in s if c.isalnum())\n    return t == t[::-1]"
    assert evaluate_code_policy(ok).verdict == "allow"


def test_policy_version_defined():
    assert POLICY_VERSION == "2"
