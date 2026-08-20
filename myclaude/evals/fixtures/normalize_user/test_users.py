from users import normalize_user


def test_normalize_user() -> None:
    assert normalize_user(
        {"name": "  Ada  ", "email": "ADA@EXAMPLE.COM"}
    ) == {
        "name": "Ada",
        "email": "ada@example.com",
    }
