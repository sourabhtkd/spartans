from solution import fizzbuzz


def test_fizzbuzz_values():
    res = fizzbuzz(15)
    assert res[0] == "1"
    assert res[2] == "Fizz"
    assert res[4] == "Buzz"
    assert res[14] == "FizzBuzz"
    assert len(res) == 15
