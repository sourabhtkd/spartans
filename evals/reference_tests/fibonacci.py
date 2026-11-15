import pytest
from solution import fibonacci


def test_fibonacci_base():
    assert fibonacci(0) == 0
    assert fibonacci(1) == 1

def test_fibonacci_values():
    assert fibonacci(5) == 5
    assert fibonacci(10) == 55

def test_fibonacci_negative():
    with pytest.raises(ValueError):
        fibonacci(-3)
