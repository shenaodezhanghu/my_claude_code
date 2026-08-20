from __future__ import annotations

from pathlib import Path

from evals.run_eval import DATASETS, FIXTURES, load_cases, score_case


def test_all_dataset_cases_have_existing_fixtures() -> None:
    cases = []
    cases.extend(load_cases(DATASETS / "tool_calling.jsonl"))
    cases.extend(load_cases(DATASETS / "coding_tasks.jsonl"))

    assert len(cases) == 20
    for case in cases:
        if case.fixture is not None:
            assert (FIXTURES / case.fixture).is_dir(), case.case_id


def test_score_case_accepts_valid_tool_trace() -> None:
    messages = [
        {
            "role": "assistant",
            "tool_calls": [
                {
                    "id": "call-1",
                    "type": "function",
                    "function": {
                        "name": "read_file",
                        "arguments": '{"path": "README.md"}',
                    },
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "call-1",
            "content": "Mini Claude",
        },
    ]
    case = load_cases(DATASETS / "tool_calling.jsonl")[0]

    errors = score_case(
        case,
        messages,
        changed=[],
        verify_passed=True,
        verify_output="ok",
        final_answer="这个项目叫 Mini Claude。",
    )

    assert errors == []


def test_coding_fixture_contains_failing_start_state() -> None:
    calculator = FIXTURES / "add_function" / "calculator.py"
    assert calculator.exists()
    assert "calculate_sum" not in calculator.read_text(encoding="utf-8")


def test_selection_files_are_jsonl() -> None:
    for path in Path(DATASETS).glob("*.jsonl"):
        assert path.read_text(encoding="utf-8").strip()
