from solution import list_sum


def test_list_sum_basic():
    assert list_sum([1, 2, 3, 4]) == 10
    assert list_sum((10, -5, 2)) == 7
    assert list_sum([]) == 0
