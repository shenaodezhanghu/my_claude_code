from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class BudgetLimits:
    max_turns: int | None = None
    max_cost_usd: float | None = None
    input_price_per_million: float = 0.0
    output_price_per_million: float = 0.0
    cache_read_price_per_million: float = 0.0


@dataclass
class BudgetState:
    limits: BudgetLimits
    turns: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    estimated_cost_usd: float = 0.0

    def record_usage(self, usage: Any | None) -> None:
        self.turns += 1
        if usage is None:
            return

        prompt_tokens = int(
            _read_field(
                usage,
                "prompt_tokens",
                _read_field(usage, "input_tokens", 0),
            )
            or 0
        )
        output_tokens = int(
            _read_field(
                usage,
                "completion_tokens",
                _read_field(usage, "output_tokens", 0),
            )
            or 0
        )
        details = _read_field(usage, "prompt_tokens_details", None)
        cached_tokens = int(
            _read_field(
                details,
                "cached_tokens",
                _read_field(
                    usage,
                    "cache_read_input_tokens",
                    _read_field(usage, "cached_tokens", 0),
                ),
            )
            or 0
        )
        creation_tokens = int(
            _read_field(usage, "cache_creation_input_tokens", 0)
            or 0
        )
        self.input_tokens += prompt_tokens
        self.output_tokens += output_tokens
        self.cache_read_tokens += cached_tokens
        self.cache_creation_tokens += creation_tokens
        # 估算费用
        normal_input = max(0, prompt_tokens - cached_tokens)
        self.estimated_cost_usd += (
                normal_input
                * self.limits.input_price_per_million
                / 1_000_000
        )
        self.estimated_cost_usd += (
                cached_tokens
                * self.limits.cache_read_price_per_million
                / 1_000_000
        )
        self.estimated_cost_usd += (
                output_tokens
                * self.limits.output_price_per_million
                / 1_000_000
        )

    def stop_reason(self) -> str | None:
        if (
                self.limits.max_turns is not None
                and self.turns >= self.limits.max_turns
        ):
            return (
                "达到模型调用轮次上限："
                f"{self.turns}/{self.limits.max_turns}"
            )
        if (
                self.limits.max_cost_usd is not None
                and self.estimated_cost_usd >= self.limits.max_cost_usd
        ):
            return (
                "达到费用上限："
                f"${self.estimated_cost_usd:.4f}/"
                f"${self.limits.max_cost_usd:.4f}"
            )
        return None

    def to_dict(self) -> dict:
        return {
            "turns": self.turns,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_read_tokens": self.cache_read_tokens,
            "cache_creation_tokens": self.cache_creation_tokens,
            "estimated_cost_usd": self.estimated_cost_usd,
        }

    def restore(self, value: dict) -> None:
        self.turns = int(value.get("turns", 0))
        self.input_tokens = int(value.get("input_tokens", 0))
        self.output_tokens = int(value.get("output_tokens", 0))
        self.cache_read_tokens = int(
            value.get("cache_read_tokens", 0)
        )
        self.cache_creation_tokens = int(
            value.get("cache_creation_tokens", 0)
        )
        self.estimated_cost_usd = float(
            value.get("estimated_cost_usd", 0.0)
        )


def _read_field(value: Any, name: str, default: Any = 0) -> Any:
    if value is None:
        return default
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)