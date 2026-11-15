from solution import is_palindrome


def test_palindrome_basic():
    assert is_palindrome("radar") is True
    assert is_palindrome("hello") is False

def test_palindrome_edges():
    assert is_palindrome("") is True
    assert is_palindrome("a") is True
    assert is_palindrome("racecar") is True
