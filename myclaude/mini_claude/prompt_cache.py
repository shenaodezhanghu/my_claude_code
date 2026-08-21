from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import time


@dataclass
class CachedText:
    value: str = ""
    mtime: float | None = None
    updated_at: float = 0.0


@dataclass
class PromptBuildCache:
    project_instruction: CachedText = field(default_factory=CachedText)
    git_context: CachedText = field(default_factory=CachedText)
    deferred_tools: CachedText = field(default_factory=CachedText)
    git_dirty: bool = True
    git_ttl_seconds: float = 5.0

    def mark_git_dirty(self) -> None:
        self.git_dirty = True

    def git_expired(self) -> bool:
        if self.git_dirty:
            return True
        return (
            time.monotonic() - self.git_context.updated_at
            > self.git_ttl_seconds
        )


def file_mtime(path: Path | None) -> float | None:
    if path is None:
        return None
    try:
        return path.stat().st_mtime
    except OSError:
        return None