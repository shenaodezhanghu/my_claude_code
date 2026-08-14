from __future__ import annotations

from dataclasses import dataclass
import os

from openai import OpenAI


TRUE_VALUES = {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class ModelCapabilities:
    explicit_cache: bool
    tool_stream_completion: bool
    usage_in_stream: bool


def create_client(
    api_key: str | None = None,
    base_url: str | None = None,
) -> OpenAI:
    resolved_key = api_key or os.environ.get("OPENAI_API_KEY")
    resolved_base = base_url or os.environ.get("OPENAI_BASE_URL")

    if not resolved_key:
        raise RuntimeError("缺少 OPENAI_API_KEY")
    if not resolved_base:
        raise RuntimeError("缺少 OPENAI_BASE_URL")

    return OpenAI(
        api_key=resolved_key,
        base_url=resolved_base,
    )


def get_model(override: str | None = None) -> str:
    value = override or os.environ.get(
        "MINI_CLAUDE_MODEL",
        "qwen-plus",
    )
    return value.strip().strip('"\'')


def get_model_capabilities() -> ModelCapabilities:
    explicit_cache = (
        os.environ.get("MINI_CLAUDE_EXPLICIT_CACHE", "")
        .strip()
        .lower()
        in TRUE_VALUES
    )
    return ModelCapabilities(
        explicit_cache=explicit_cache,
        # OpenAI Chat Completions 没有“单个工具调用完成”的可靠事件。
        tool_stream_completion=False,
        usage_in_stream=True,
    )