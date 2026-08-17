from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class SessionWorkspace:
    session_id: str
    root: Path
    messages_file: Path
    state_file: Path
    working_memory_file: Path
    tool_results_dir: Path


def create_session_workspace(
    project_root: Path,
    session_id: str,
) -> SessionWorkspace:
    root = (
        project_root
        / ".mini-agent"
        / "sessions"
        / session_id
    )
    tool_results_dir = root / "tool-results"
    tool_results_dir.mkdir(parents=True, exist_ok=True)

    return SessionWorkspace(
        session_id=session_id,
        root=root,
        messages_file=root / "messages.json",
        state_file=root / "state.json",
        working_memory_file=root / "working-memory.json",
        tool_results_dir=tool_results_dir,
    )