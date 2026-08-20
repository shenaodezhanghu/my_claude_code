from calculator import calculate_sum, subtract


def test_calculate_sum() -> None:
    assert calculate_sum(2, 3) == 5


def test_subtract_is_preserved() -> None:
    assert subtract(5, 3) == 2