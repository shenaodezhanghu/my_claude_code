from __future__ import annotations

from typing import Any
from dataclasses import dataclass, field, asdict

@dataclass(frozen=True)
class EvalCase:
    case_id: str
    category: str
    prompt: str
    fixture: str | None = None
    expected_tools: list[str] = field(default_factory=list)
    forbidden_tools: list[str] = field(default_factory=list)
    expected_calls: list[dict[str, Any]] = field(default_factory=list)
    required_order: list[list[str]] = field(default_factory=list)
    expected_parallel_groups: list[list[str]] = field(default_factory=list)
    reject_duplicate_reads: bool = False
    expected_files: list[str] = field(default_factory=list)
    forbidden_files: list[str] = field(default_factory=list)
    verify_command: list[str] = field(default_factory=list)
    expected_answer: str | None = None
    max_tool_calls: int | None = None
    notes: str | None = None

@dataclass
class EvalResult:
    case_id: str
    category: str
    profile: str
    passed: bool
    final_answer: str
    tool_calls: list[dict[str, Any]]
    changed_files: list[str]
    duration_seconds: float
    model_turns: int = 0
    total_tool_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    estimated_cost_usd: float = 0.0
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
