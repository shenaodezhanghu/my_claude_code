from __future__ import annotations

from datetime import datetime
from pathlib import Path

LARGE_RESULT_BYTES = 30 * 1024
MAX_RESULT_CHARS = 50_000
PREVIEW_LINES = 200
COMPACT_TRIGGER_CHARS = 80_000  # 历史超过约 8 万字符时触发压缩
KEEP_RECENT_TURNS = 3           # 压缩时保留最近 3 轮对话


def truncate_result(result: str) -> str:
    if len(result) <= MAX_RESULT_CHARS:
        return result

    keep_each = (MAX_RESULT_CHARS - 80) // 2
    omitted = len(result) - keep_each * 2
    return (
        result[:keep_each]
        + f"\n\n[... truncated {omitted} characters ...]\n\n"
        + result[-keep_each:]
    )


def persist_large_result(
    tool_name: str,
    result: str,
    project_root: Path,
) -> str:
    size = len(result.encode("utf-8"))
    if size <= LARGE_RESULT_BYTES:
        return truncate_result(result)

    result_dir = project_root / ".mini-agent" / "tool-results"
    result_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    result_file = result_dir / f"{tool_name}-{timestamp}.txt"
    result_file.write_text(result, encoding="utf-8")

    lines = result.splitlines()
    preview = "\n".join(lines[:PREVIEW_LINES])
    relative_path = result_file.relative_to(project_root)

    message = (
        f"[Result too large: {size / 1024:.1f} KB, {len(lines)} lines. "
        f"Full output saved to {relative_path}. "
        "Use read_file if the complete result is needed.]\n\n"
        f"Preview:\n{preview}"
    )
    return truncate_result(message)


def history_size(messages: list[dict]) -> int:
    return len(str(messages))


def find_recent_boundary(
    messages: list[dict],
    keep_turns: int = KEEP_RECENT_TURNS,
) -> int:
    user_indexes = [
        index
        for index, message in enumerate(messages)
        if message.get("role") == "user"
        and isinstance(message.get("content"), str)
    ]

    if len(user_indexes) <= keep_turns:
        return 0
    return user_indexes[-keep_turns]


def render_for_summary(messages: list[dict]) -> str:
    lines: list[str] = []

    for message in messages:
        role = str(message.get("role", "unknown"))
        content = message.get("content")

        if isinstance(content, str) and content:
            lines.append(f"{role}: {content}")
        elif message.get("tool_calls"):
            names = [
                call.get("function", {}).get("name", "unknown")
                for call in message["tool_calls"]
            ]
            lines.append(
                f"assistant requested tools: {', '.join(names)}"
            )
        else:
            lines.append(f"{role}: [structured message]")

    return "\n".join(lines)


def summarize_messages(
    messages: list[dict],
    client,
    model: str,
) -> str:
    transcript = render_for_summary(messages)
    response = client.chat.completions.create(
        model=model,
        messages=[
            {
                "role": "system",
                "content": (
                    "总结 Coding Agent 的历史。保留用户目标、关键决定、"
                    "文件路径、已完成修改、错误、验证结果和待办事项。"
                    "不要虚构未执行的操作。"
                ),
            },
            {"role": "user", "content": transcript},
        ],
    )
    return response.choices[0].message.content or "无可用摘要"


def maybe_compact(
    messages: list[dict],
    client,
    model: str,
) -> list[dict]:
    if history_size(messages) < COMPACT_TRIGGER_CHARS:
        return messages

    boundary = find_recent_boundary(messages)
    if boundary == 0:
        return messages

    older = messages[:boundary]
    recent = messages[boundary:]
    summary = summarize_messages(older, client, model)

    print(f"(已将 {len(older)} 条旧消息压缩为摘要)")
    return [
        {
            "role": "user",
            "content": f"[Earlier conversation summary]\n{summary}",
        },
        {
            "role": "assistant",
            "content": "已了解此前摘要，将根据最近任务继续。",
        },
        *recent,
    ]