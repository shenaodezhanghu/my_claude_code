from __future__ import annotations

import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


IGNORED_PARTS = {
    ".git",
    ".mini-agent",
    ".pytest_cache",
    "__pycache__",
}


def prepare_workspace(
    fixtures_root: Path,
    fixture: str | None,
) -> tuple[Path, Path]:
    temp_root = Path(tempfile.mkdtemp(prefix="mini-eval-"))
    workspace = temp_root / "workspace"

    if fixture is None:
        workspace.mkdir()
    else:
        source = fixtures_root / fixture
        if not source.is_dir():
            raise FileNotFoundError(f"Fixture 不存在：{source}")
        shutil.copytree(source, workspace)

    return temp_root, workspace


def snapshot_files(root: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(root)
        if any(part in IGNORED_PARTS for part in relative.parts):
            continue
        result[relative.as_posix()] = hashlib.sha256(
            path.read_bytes()
        ).hexdigest()
    return result


def changed_files(
    before: dict[str, str],
    after: dict[str, str],
) -> list[str]:
    return sorted(
        name
        for name in set(before) | set(after)
        if before.get(name) != after.get(name)
    )


def run_verify(
    command: list[str],
    cwd: Path,
) -> tuple[bool, str]:
    if not command:
        return True, "未配置验证命令"

    expanded_command = [
        sys.executable if item == "__PYTHON__" else item
        for item in command
    ]
    env = os.environ.copy()
    env["PYTHONPATH"] = str(cwd)

    try:
        completed = subprocess.run(
            expanded_command,
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
            shell=False,
            env=env,
        )
    except subprocess.TimeoutExpired as exc:
        return False, f"验证命令超时：{exc}"

    output = "\n".join(
        item
        for item in (completed.stdout, completed.stderr)
        if item
    )
    return completed.returncode == 0, output
