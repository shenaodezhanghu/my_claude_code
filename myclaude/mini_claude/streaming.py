from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class StreamResult:
    message: dict
    finish_reason: str
    usage: Any | None = None


def collect_stream(
    stream: Iterable[Any],
    on_text: Callable[[str], None],
) -> StreamResult:
    content_parts: list[str] = []
    tool_calls: dict[int, dict[str, str]] = {}
    finish_reason = "stop"
    usage = None

    for chunk in stream:
        if getattr(chunk, "usage", None) is not None:
            usage = chunk.usage

        choices = getattr(chunk, "choices", None) or []
        if not choices:
            continue

        choice = choices[0]
        delta = choice.delta

        if delta.content:
            on_text(delta.content)
            content_parts.append(delta.content)

        if delta.tool_calls:
            for part in delta.tool_calls:
                current = tool_calls.setdefault(
                    part.index,
                    {
                        "id": "",
                        "name": "",
                        "arguments": "",
                    },
                )
                if part.id:
                    current["id"] = part.id
                if part.function and part.function.name:
                    current["name"] += part.function.name
                if part.function and part.function.arguments:
                    current["arguments"] += part.function.arguments

        if choice.finish_reason:
            finish_reason = choice.finish_reason

    content = "".join(content_parts)
    assembled_calls = [
        {
            "id": item["id"],
            "type": "function",
            "function": {
                "name": item["name"],
                "arguments": item["arguments"],
            },
        }
        for _, item in sorted(tool_calls.items())
    ]

    message: dict = {
        "role": "assistant",
        "content": content or None,
    }
    if assembled_calls:
        message["tool_calls"] = assembled_calls

    return StreamResult(
        message=message,
        finish_reason=finish_reason,
        usage=usage,
    )