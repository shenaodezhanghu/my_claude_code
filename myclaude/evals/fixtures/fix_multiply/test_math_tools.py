from math_tools import divide, multiply


def test_multiply() -> None:
    assert multiply(4, 5) == 20


def test_divide_is_preserved() -> None:
    assert divide(10, 2) == 5
