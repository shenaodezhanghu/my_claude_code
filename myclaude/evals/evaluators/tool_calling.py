from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import json
import re
from typing import Any


FAILED_RESULT_PREFIXES = (
    "error:",
    "action denied:",
    "user denied",
    "cancelled",
)


@dataclass
class ToolEvent:
    order: int
    batch: int
    call_id: str
    name: str
    arguments: dict[str, Any]
    result: str | None = None

    @property
    def succeeded(self) -> bool:
        if self.result is None:
            return False
        return not self.result.strip().lower().startswith(
            FAILED_RESULT_PREFIXES
        )

def extract_tool_trace(messages: list[dict]) -> list[ToolEvent]:
    events: list[ToolEvent] = []
    by_id: dict[str, ToolEvent] = {}
    batch = -1

    for message in messages:
        if message.get("role") == "assistant":
            raw_calls = message.get("tool_calls") or []
            if not raw_calls:
                continue
            batch += 1

            for raw_call in raw_calls:
                function = raw_call.get("function") or {}
                raw_arguments = function.get("arguments") or "{}"
                try:
                    arguments = json.loads(raw_arguments)
                except json.JSONDecodeError:
                    arguments = {"__invalid_json__": raw_arguments}

                event = ToolEvent(
                    order=len(events),
                    batch=batch,
                    call_id=str(raw_call.get("id") or ""),
                    name=str(function.get("name") or ""),
                    arguments=arguments,
                )
                events.append(event)
                by_id[event.call_id] = event
            continue

        if message.get("role") == "tool":
            call_id = str(message.get("tool_call_id") or "")
            event = by_id.get(call_id)
            if event is not None:
                event.result = str(message.get("content") or "")

    return events

def extract_tool_calls(messages: list[dict]) -> list[dict[str, Any]]:
    return [
        {
            "id": event.call_id,
            "name": event.name,
            "arguments": event.arguments,
            "batch": event.batch,
            "succeeded": event.succeeded,
            "result": event.result,
        }
        for event in extract_tool_trace(messages)
    ]

def tool_names(events: list[ToolEvent]) -> list[str]:
    return [event.name for event in events]

def score_tool_names(
        events: list[ToolEvent],
        expected: list[str],
        forbidden: list[str],
        max_calls: int | None,
) -> list[str]:
    names = tool_names(events)
    errors: list[str] = []

    for name in expected:
        if name not in names:
            errors.append(f"缺少必要工具：{name}")
    for name in forbidden:
        if name in names:
            errors.append(f"调用了禁止工具：{name}")
    if max_calls is not None and len(names) > max_calls:
        errors.append(f"工具调用过多：{len(names)} > {max_calls}")
    return errors


def normalize_path(value: Any) -> str:
    normalized = str(value or "").replace("\\", "/")
    normalized = re.sub(r"/+", "/", normalized)
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized.casefold()


def value_matches(
    actual: Any,
    expected: Any,
    key: str | None = None,
) -> bool:
    if isinstance(expected, dict):
        return (
            isinstance(actual, dict)
            and all(
                child_key in actual
                and value_matches(
                    actual[child_key],
                    value,
                    child_key,
                )
                for child_key, value in expected.items()
            )
        )
    if isinstance(expected, list):
        return (
            isinstance(actual, list)
            and len(actual) == len(expected)
            and all(
                value_matches(a, e)
                for a, e in zip(actual, expected)
            )
        )
    if key == "path" and isinstance(expected, str):
        return normalize_path(actual) == normalize_path(expected)
    return actual == expected


def score_expected_calls(
    events: list[ToolEvent],
    expected_calls: list[dict[str, Any]],
) -> list[str]:
    errors: list[str] = []
    for expected in expected_calls:
        name = str(expected["name"])
        arguments = expected.get("arguments", {})
        matched = any(
            event.name == name
            and value_matches(event.arguments, arguments)
            for event in events
        )
        if not matched:
            errors.append(
                f"没有找到参数匹配的调用：{name} {arguments}"
            )
    return errors


def score_required_order(
    events: list[ToolEvent],
    required_order: list[list[str]],
) -> list[str]:
    """检查具有依赖关系的工具是否按要求先后调用。"""
    errors: list[str] = []
    for pair in required_order:
        if len(pair) != 2:
            errors.append(f"非法顺序规则：{pair}")
            continue

        before, after = pair
        before_events = [event for event in events if event.name == before]
        after_events = [event for event in events if event.name == after]
        if not before_events or not after_events:
            continue

        if min(event.order for event in before_events) >= min(
            event.order for event in after_events
        ):
            errors.append(f"工具顺序错误：{before} 应早于 {after}")
    return errors


def score_parallel_groups(
    events: list[ToolEvent],
    expected_groups: list[list[str]],
    concurrency_safe_tools: set[str],
) -> list[str]:
    """检查工具是否由同一 Assistant 批次发出且允许并发。"""
    errors: list[str] = []
    batches: dict[int, list[str]] = {}
    for event in events:
        batches.setdefault(event.batch, []).append(event.name)

    for group in expected_groups:
        expected = Counter(group)
        if not any(
            expected <= Counter(names)
            for names in batches.values()
        ):
            errors.append(f"没有在同一批次调用：{group}")

        unsafe = sorted(
            name
            for name in set(group)
            if name not in concurrency_safe_tools
        )
        if unsafe:
            errors.append(
                "并行组包含非并发安全工具：" + ", ".join(unsafe)
            )
    return errors


def score_duplicate_reads(events: list[ToolEvent]) -> list[str]:
    """只拦截同一文件版本的重复读取，修改后的重读是合法的。"""
    errors: list[str] = []
    versions: dict[str, int] = {}
    last_read_version: dict[str, int] = {}

    for event in events:
        path = normalize_path(event.arguments.get("path"))
        if not path:
            continue
        version = versions.get(path, 0)

        if event.name == "read_file" and event.succeeded:
            if last_read_version.get(path) == version:
                errors.append(f"重复读取未变化文件：{path}")
            last_read_version[path] = version
        elif (
            event.name in {"write_file", "edit_file"}
            and event.succeeded
        ):
            versions[path] = version + 1
    return errors


def score_read_before_edit(events: list[ToolEvent]) -> list[str]:
    """检查 edit_file 之前是否成功读取过同一路径。"""
    errors: list[str] = []
    successful_reads: set[str] = set()

    for event in events:
        path = normalize_path(event.arguments.get("path"))
        if event.name == "read_file" and event.succeeded and path:
            successful_reads.add(path)
            continue
        if event.name == "edit_file" and path not in successful_reads:
            errors.append(f"edit_file 前没有成功读取同一文件：{path}")
    return errors


def activated_names(result: str | None) -> set[str]:
    """从 tool_search 返回的工具描述中提取被激活工具名。"""
    if not result:
        return set()
    return set(re.findall(r'"name"\s*:\s*"([^"]+)"', result))


def score_deferred_activation(
    events: list[ToolEvent],
    deferred_tools: set[str],
) -> list[str]:
    """Deferred Tool 必须由成功的 tool_search 在更早批次激活。"""
    errors: list[str] = []
    activated_at: dict[str, int] = {}

    for event in events:
        if event.name == "tool_search" and event.succeeded:
            for name in activated_names(event.result):
                if name in deferred_tools:
                    activated_at.setdefault(name, event.batch)
            continue

        if event.name not in deferred_tools:
            continue

        search_batch = activated_at.get(event.name)
        if search_batch is None:
            errors.append(
                f"Deferred Tool 未经 tool_search 激活：{event.name}"
            )
        elif event.batch <= search_batch:
            errors.append(
                "Deferred Tool 必须在 tool_search 的下一批调用："
                f"{event.name}"
            )
    return errors


def score_tool_trace(
    events: list[ToolEvent],
    *,
    expected_tools: list[str],
    forbidden_tools: list[str],
    expected_calls: list[dict[str, Any]],
    required_order: list[list[str]],
    expected_parallel_groups: list[list[str]],
    max_calls: int | None,
    reject_duplicate_reads: bool,
    concurrency_safe_tools: set[str],
    deferred_tools: set[str],
) -> list[str]:
    """组合所有工具轨迹规则，返回可直接写入报告的错误。"""
    errors = score_tool_names(
        events,
        expected_tools,
        forbidden_tools,
        max_calls,
    )
    errors.extend(score_expected_calls(events, expected_calls))
    errors.extend(score_required_order(events, required_order))
    errors.extend(
        score_parallel_groups(
            events,
            expected_parallel_groups,
            concurrency_safe_tools,
        )
    )
    if reject_duplicate_reads:
        errors.extend(score_duplicate_reads(events))
    errors.extend(score_read_before_edit(events))
    errors.extend(score_deferred_activation(events, deferred_tools))
    return errors


