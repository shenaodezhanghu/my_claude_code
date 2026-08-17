from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path


@dataclass
class WorkingMemoryCandidate:
    content: str
    suggested_type: str
    tags: list[str] = field(default_factory=list)
    importance: float = 0.0
    reason: str = ""

    def to_dict(self) -> dict:
        return {
            "content": self.content,
            "suggested_type": self.suggested_type,
            "tags": self.tags,
            "importance": self.importance,
            "reason": self.reason,
        }

    @classmethod
    def from_dict(cls, value: dict) -> "WorkingMemoryCandidate":
        suggested_type = str(
            value.get("suggested_type", "episodic")
        )
        if suggested_type not in {"episodic", "semantic"}:
            suggested_type = "episodic"
        return cls(
            content=str(value.get("content", "")),
            suggested_type=suggested_type,
            tags=[str(item) for item in value.get("tags", [])],
            importance=float(value.get("importance", 0.0)),
            reason=str(value.get("reason", "")),
        )


@dataclass
class WorkingMemory:
    goal: str = ""
    plan: list[str] = field(default_factory=list)
    observations: list[str] = field(default_factory=list)
    todos: list[str] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
    memory_candidates: list[WorkingMemoryCandidate] = field(
        default_factory=list
    )

    def to_dict(self) -> dict:
        return {
            "goal": self.goal,
            "plan": self.plan,
            "observations": self.observations,
            "todos": self.todos,
            "blockers": self.blockers,
            "memory_candidates": [
                candidate.to_dict()
                for candidate in self.memory_candidates
            ],
        }

    @classmethod
    def from_dict(cls, value: dict) -> "WorkingMemory":
        return cls(
            goal=str(value.get("goal", "")),
            plan=list(value.get("plan", [])),
            observations=list(value.get("observations", [])),
            todos=list(value.get("todos", [])),
            blockers=list(value.get("blockers", [])),
            memory_candidates=[
                WorkingMemoryCandidate.from_dict(item)
                for item in value.get("memory_candidates", [])
                if isinstance(item, dict)
            ],
        )


def load_working_memory(path: Path) -> WorkingMemory:
    if not path.exists():
        return WorkingMemory()
    try:
        return WorkingMemory.from_dict(
            json.loads(path.read_text(encoding="utf-8"))
        )
    except Exception:
        return WorkingMemory()


def save_working_memory(
    path: Path,
    memory: WorkingMemory,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(memory.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )