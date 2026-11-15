import pytest
from solution import factorial


def test_factorial_base():
    assert factorial(0) == 1
    assert factorial(1) == 1

def test_factorial_positives():
    assert factorial(5) == 120
    assert factorial(6) == 720

def test_factorial_negative():
    with pytest.raises(ValueError):
        factorial(-1)
