from solution import is_palindrome


def test_ignores_case():
    assert is_palindrome("Racecar") is True
    assert is_palindrome("RaDaR") is True


def test_ignores_spaces_and_punctuation():
    assert is_palindrome("A man, a plan, a canal: Panama") is True
    assert is_palindrome("Was it a car or a cat I saw?") is True


def test_still_rejects_non_palindromes():
    assert is_palindrome("Hello, world") is False
