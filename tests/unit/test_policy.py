from codeforge.policy.code_policy import evaluate_code_policy


def test_student_b_os_remove_denied():
    code = "import os\nos.remove('/etc/passwd')"
    decision = evaluate_code_policy(code)
    assert decision.verdict == "deny"
    assert any("os" in r for r in decision.reasons)


def test_shutil_rmtree_denied():
    code = "from shutil import rmtree\nrmtree('/tmp')"
    decision = evaluate_code_policy(code)
    assert decision.verdict == "deny"
    assert any("shutil" in r for r in decision.reasons)


def test_dunder_import_call_denied():
    code = "mod = __import__('os')"
    decision = evaluate_code_policy(code)
    assert decision.verdict == "deny"
    assert any("__import__" in r for r in decision.reasons)


def test_getattr_builtins_eval_denied():
    code = "fn = getattr(__builtins__, '__import__')"
    decision = evaluate_code_policy(code)
    assert decision.verdict == "deny"


def test_subclasses_escape_denied():
    code = "classes = ().__class__.__subclasses__()"
    decision = evaluate_code_policy(code)
    assert decision.verdict == "deny"
    assert any("__class__" in r or "__subclasses__" in r for r in decision.reasons)


def test_open_file_needs_approval():
    code = "with open('output.txt', 'w') as f:\n    f.write('hello')"
    decision = evaluate_code_policy(code)
    assert decision.verdict == "needs_approval"
    assert any("open()" in r for r in decision.reasons)


def test_requests_import_needs_approval():
    code = "import requests\nresp = requests.get('https://example.com')"
    decision = evaluate_code_policy(code)
    assert decision.verdict == "needs_approval"
    assert any("requests" in r for r in decision.reasons)


def test_clean_palindrome_allowed():
    code = (
        "def is_palindrome(s: str) -> bool:\n"
        "    return s == s[::-1]\n"
    )
    decision = evaluate_code_policy(code)
    assert decision.verdict == "allow"
    assert len(decision.reasons) == 0


def test_syntax_error_denied():
    code = "def invalid_syntax(:"
    decision = evaluate_code_policy(code)
    assert decision.verdict == "deny"
    assert any("Unparseable" in r for r in decision.reasons)


def test_future_import_allowed_but_private_modules_denied():
    from codeforge.policy.code_policy import evaluate_code_policy

    assert evaluate_code_policy("from __future__ import annotations\nx = 1").verdict == "allow"
    assert evaluate_code_policy("import _socket").verdict == "deny"
