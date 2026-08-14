from __future__ import annotations

import json
from pathlib import Path

from mini_claude.context import persist_large_result
from mini_claude.tools.base import ToolContext
from mini_claude.tools.registry import ToolRegistry


EXPLORE_TOOLS = {
    "read_file",
    "list_files",
    "grep_search",
}

SUBAGENT_PROMPT = """
你是只读代码探索子 Agent。
你只能读取、列出和搜索文件，不能修改文件、运行 Shell 或创建其他 Agent。
完成调查后返回简洁总结，并包含关键文件路径和结论。
"""


def _readonly_schemas(registry: ToolRegistry) -> list[dict]:
    return [
        schema
        for schema in registry.schemas()
        if schema["function"]["name"] in EXPLORE_TOOLS
    ]


def run_sub_agent(
    task: str,
    client,
    model: str,
    registry: ToolRegistry,
    project_root: Path,
) -> str:
    messages: list[dict] = [
        {"role": "user", "content": task}
    ]
    context = ToolContext(project_root=project_root)
    schemas = _readonly_schemas(registry)

    while True:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": SUBAGENT_PROMPT},
                *messages,
            ],
            tools=schemas,
        )

        message = response.choices[0].message.model_dump(
            exclude_none=True
        )
        messages.append(message)

        tool_calls = message.get("tool_calls") or []
        if not tool_calls:
            return str(message.get("content") or "")

        for tool_call in tool_calls:
            name = tool_call["function"]["name"]
            try:
                arguments = json.loads(
                    tool_call["function"]["arguments"]
                )
            except json.JSONDecodeError as exc:
                result = f"Error: invalid tool arguments: {exc}"
            else:
                if name not in EXPLORE_TOOLS:
                    result = "Denied: 子 Agent 只允许使用只读探索工具。"
                else:
                    full_result = registry.execute(
                        name,
                        arguments,
                        context,
                    )
                    result = persist_large_result(
                        name,
                        full_result,
                        project_root,
                    )

            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call["id"],
                    "content": result,
                }
            )