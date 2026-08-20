from pathlib import Path
import shutil
import sys

from evals.workspace import (
    changed_files,
    prepare_workspace,
    run_verify,
    snapshot_files,
)


FIXTURES = Path(__file__).resolve().parent / "fixtures"


def test_fixture_is_copied_and_source_is_unchanged() -> None:
    source = FIXTURES / "add_function" / "calculator.py"
    source_content = source.read_text(encoding="utf-8")
    temp_root, workspace = prepare_workspace(
        FIXTURES,
        "add_function",
    )
    try:
        before = snapshot_files(workspace)
        target = workspace / "calculator.py"
        target.write_text(
            source_content + "\n# changed in temporary workspace\n",
            encoding="utf-8",
        )
        after = snapshot_files(workspace)

        assert changed_files(before, after) == ["calculator.py"]
        assert source.read_text(encoding="utf-8") == source_content
    finally:
        shutil.rmtree(temp_root)


def test_verify_command_runs_inside_workspace() -> None:
    temp_root, workspace = prepare_workspace(FIXTURES, None)
    try:
        passed, output = run_verify(
            [sys.executable, "-c", "print('verify-ok')"],
            workspace,
        )
        assert passed
        assert "verify-ok" in output
    finally:
        shutil.rmtree(temp_root)


def test_verify_command_expands_current_python() -> None:
    temp_root, workspace = prepare_workspace(FIXTURES, None)
    try:
        passed, output = run_verify(
            ["__PYTHON__", "-c", "import sys; print(sys.executable)"],
            workspace,
        )
        assert passed
        assert sys.executable in output
    finally:
        shutil.rmtree(temp_root)
