import pytest
from solution import max_of_list


def test_max_of_list():
    assert max_of_list([1, 10, 3]) == 10
    assert max_of_list([-5, -1, -20]) == -1
    with pytest.raises(ValueError):
        max_of_list([])
