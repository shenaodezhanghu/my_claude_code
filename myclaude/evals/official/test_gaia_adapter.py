from __future__ import annotations

from evals.official.gaia_adapter import (
    copy_attachment,
    extract_final_answer,
    normalize_answer,
)


def test_extract_final_answer_uses_last_final_answer_line() -> None:
    text = "reasoning\nFinal answer: 12\nmore\nFinal answer: 3"

    assert extract_final_answer(text) == "3"


def test_extract_final_answer_falls_back_to_full_text() -> None:
    assert extract_final_answer("Ball 3") == "Ball 3"


def test_normalize_answer_ignores_space_case_and_simple_punctuation() -> None:
    assert normalize_answer("  Ball 3. ") == normalize_answer("ball 3")


def test_copy_attachment_reports_missing_path(tmp_path) -> None:
    name, error = copy_attachment(
        {"file_path": str(tmp_path / "missing.txt")},
        tmp_path / "workspace",
    )

    assert name is None
    assert error is not None
    assert "附件路径不存在" in error
