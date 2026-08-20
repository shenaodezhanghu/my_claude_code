from parser import parse_port


def test_parse_port_valid_number() -> None:
    assert parse_port("9000") == 9000


def test_parse_port_invalid_value() -> None:
    assert parse_port("abc") == 8000


def test_parse_port_empty_value() -> None:
    assert parse_port("") == 8000
