from todo import first_pending


def test_first_pending_returns_first_unfinished_title() -> None:
    todos = [
        {"title": "done item", "done": True},
        {"title": "write eval", "done": False},
        {"title": "ship", "done": False},
    ]
    assert first_pending(todos) == "write eval"


def test_first_pending_returns_none_when_all_done() -> None:
    assert first_pending([{"title": "done", "done": True}]) is None
