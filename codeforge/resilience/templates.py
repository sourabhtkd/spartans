import re
from typing import NamedTuple


class Template(NamedTuple):
    name: str
    patterns: list[str]
    code: str
    tests: str


_TEMPLATES: list[Template] = [
    Template(
        name="palindrome",
        patterns=[r"palindrom"],
        code=(
            "def is_palindrome(s: str) -> bool:\n"
            "    \"\"\"Checks if a string is a palindrome, ignoring case, spaces and punctuation.\"\"\"\n"
            "    cleaned = [c.lower() for c in s if c.isalnum()]\n"
            "    return cleaned == cleaned[::-1]\n"
        ),
        tests=(
            "from solution import is_palindrome\n\n"
            "def test_palindrome_cases():\n"
            "    assert is_palindrome('radar') is True\n"
            "    assert is_palindrome('hello') is False\n"
            "    assert is_palindrome('') is True\n"
            "    assert is_palindrome('a') is True\n"
            "    assert is_palindrome('Racecar') is True\n"
            "    assert is_palindrome('A man, a plan, a canal: Panama') is True\n"
        ),
    ),
    Template(
        name="factorial",
        patterns=[r"\bfactorial\b"],
        code=(
            "def factorial(n: int) -> int:\n"
            "    \"\"\"Calculates the factorial of a non-negative integer n.\"\"\"\n"
            "    if n < 0:\n"
            "        raise ValueError('n must be non-negative')\n"
            "    res = 1\n"
            "    for i in range(2, n + 1):\n"
            "        res *= i\n"
            "    return res\n"
        ),
        tests=(
            "from solution import factorial\nimport pytest\n\n"
            "def test_factorial_cases():\n"
            "    assert factorial(0) == 1\n"
            "    assert factorial(1) == 1\n"
            "    assert factorial(5) == 120\n"
            "    with pytest.raises(ValueError):\n"
            "        factorial(-1)\n"
        ),
    ),
    Template(
        name="fibonacci",
        patterns=[r"\bfibonacci\b", r"\bfib\b"],
        code=(
            "def fibonacci(n: int) -> int:\n"
            "    \"\"\"Returns the n-th Fibonacci number (0-indexed: fib(0)=0, fib(1)=1).\"\"\"\n"
            "    if n < 0:\n"
            "        raise ValueError('n must be non-negative')\n"
            "    if n in (0, 1):\n"
            "        return n\n"
            "    a, b = 0, 1\n"
            "    for _ in range(2, n + 1):\n"
            "        a, b = b, a + b\n"
            "    return b\n"
        ),
        tests=(
            "from solution import fibonacci\nimport pytest\n\n"
            "def test_fibonacci_cases():\n"
            "    assert fibonacci(0) == 0\n"
            "    assert fibonacci(1) == 1\n"
            "    assert fibonacci(6) == 8\n"
            "    with pytest.raises(ValueError):\n"
            "        fibonacci(-2)\n"
        ),
    ),
    Template(
        name="fizzbuzz",
        patterns=[r"fizz\s*_?\s*buzz"],
        code=(
            "def fizzbuzz(n: int) -> list[str]:\n"
            "    \"\"\"Generates FizzBuzz sequence from 1 to n.\"\"\"\n"
            "    res = []\n"
            "    for i in range(1, n + 1):\n"
            "        if i % 15 == 0:\n"
            "            res.append('FizzBuzz')\n"
            "        elif i % 3 == 0:\n"
            "            res.append('Fizz')\n"
            "        elif i % 5 == 0:\n"
            "            res.append('Buzz')\n"
            "        else:\n"
            "            res.append(str(i))\n"
            "    return res\n"
        ),
        tests=(
            "from solution import fizzbuzz\n\n"
            "def test_fizzbuzz_cases():\n"
            "    res = fizzbuzz(15)\n"
            "    assert res[0] == '1'\n"
            "    assert res[2] == 'Fizz'\n"
            "    assert res[4] == 'Buzz'\n"
            "    assert res[14] == 'FizzBuzz'\n"
        ),
    ),
    Template(
        name="list_sum",
        patterns=[
            r"\blist_sum\b",
            r"\bsum_numbers\b",
            r"\b(?:sum|total|add up)\b.*\b(?:list|numbers|array|sequence|tuple|elements|values|items)\b",
        ],
        code=(
            "from typing import Sequence\n\n"
            "def list_sum(nums: Sequence[int | float]) -> int | float:\n"
            "    \"\"\"Calculates sum of elements in list or tuple.\"\"\"\n"
            "    return sum(nums)\n"
        ),
        tests=(
            "from solution import list_sum\n\n"
            "def test_list_sum_cases():\n"
            "    assert list_sum([1, 2, 3]) == 6\n"
            "    assert list_sum((4, 5, -1)) == 8\n"
            "    assert list_sum([]) == 0\n"
        ),
    ),
    Template(
        name="reverse_string",
        patterns=[
            r"\breverse_string\b",
            r"\breverse\b.*\b(?:string|text|word|characters)\b",
            r"\b(?:string|text)\b.*\b(?:reverse|backwards?)\b",
        ],
        code=(
            "def reverse_string(s: str) -> str:\n"
            "    \"\"\"Reverses a string.\"\"\"\n"
            "    return s[::-1]\n"
        ),
        tests=(
            "from solution import reverse_string\n\n"
            "def test_reverse_cases():\n"
            "    assert reverse_string('abc') == 'cba'\n"
            "    assert reverse_string('') == ''\n"
            "    assert reverse_string('radar') == 'radar'\n"
        ),
    ),
    Template(
        name="is_prime",
        patterns=[r"\bprime\b", r"\bis_prime\b"],
        code=(
            "def is_prime(n: int) -> bool:\n"
            "    \"\"\"Determines if an integer n is prime.\"\"\"\n"
            "    if n < 2:\n"
            "        return False\n"
            "    for i in range(2, int(n**0.5) + 1):\n"
            "        if n % i == 0:\n"
            "            return False\n"
            "    return True\n"
        ),
        tests=(
            "from solution import is_prime\n\n"
            "def test_is_prime_cases():\n"
            "    assert is_prime(2) is True\n"
            "    assert is_prime(4) is False\n"
            "    assert is_prime(17) is True\n"
            "    assert is_prime(1) is False\n"
        ),
    ),
    Template(
        name="max_of_list",
        patterns=[
            r"\bmax_of_list\b",
            r"\bfind_max\b",
            r"\b(?:max|maximum|largest|biggest|greatest)\b.*\b(?:list|sequence|array|numbers|elements|values)\b",
        ],
        code=(
            "from typing import Sequence\n\n"
            "def max_of_list(nums: Sequence[int | float]) -> int | float:\n"
            "    \"\"\"Finds the maximum value in a sequence.\"\"\"\n"
            "    if not nums:\n"
            "        raise ValueError('Sequence must not be empty')\n"
            "    return max(nums)\n"
        ),
        tests=(
            "from solution import max_of_list\nimport pytest\n\n"
            "def test_max_cases():\n"
            "    assert max_of_list([1, 5, 3]) == 5\n"
            "    assert max_of_list([-10, -2]) == -2\n"
            "    assert max_of_list([7]) == 7\n"
            "    assert max_of_list((2.5, 1.5)) == 2.5\n"
            "    with pytest.raises(ValueError):\n"
            "        max_of_list([])\n"
        ),
    ),
]


def match_template(text: str) -> Template | None:
    """Matches text against deterministic code templates using plain-Python regex rules."""
    text_clean = text.lower()
    for t in _TEMPLATES:
        for pat in t.patterns:
            if re.search(pat, text_clean):
                return t
    return None
