# 第十一章 多 Agent：只读子 Agent

> 本章从第十章最终代码继续。主 Agent 仍然只有一个；新增的子 Agent 是一次任务内临时创建的独立消息循环。它只获得三个只读工具，结束后只把总结返回主 Agent。

## 11.1 为什么需要子 Agent

如果主 Agent 为了调查一个问题读取大量文件，中间过程会全部进入主会话，快速消耗上下文。子 Agent 使用独立的 `messages`：

```text
主 Agent
  → agent(task="调查权限实现")
  → 子 Agent 使用全新 messages
  → 只读搜索和分析
  → 返回一段总结
  → 主 Agent 只保存这段总结
```

本章采用原教程的 fork-return 模式，不实现多个 Agent 共享同一历史，也不允许子 Agent 递归创建子 Agent。

## 11.2 本章结构

```text
mini_claude/
├── agent.py
├── subagent.py                 # 本章新增
└── tools/
    ├── __init__.py
    ├── agent_tool.py           # 本章新增
    └── base.py                 # ToolContext 增加回调
```

## 11.3 为 ToolContext 增加子 Agent 回调

在 `tools/base.py` 的导入中加入：

```python
from collections.abc import Callable
```

把 `ToolContext` 扩展为：

```python
@dataclass
class ToolContext:
    project_root: Path
    read_file_state: dict[str, float] = field(default_factory=dict)
    subagent_runner: Callable[[str], str] | None = None
```

工具层不应该自己创建模型客户端。它只调用当前 Agent 放入 Context 的回调，从而避免 `tools/` 反向依赖 `agent.py`。

## 11.4 创建 agent 工具

创建 `mini_claude/tools/agent_tool.py`：

```python
from __future__ import annotations

from .base import Tool, ToolContext


class AgentTool(Tool):
    read_only = True

    def __init__(self) -> None:
        super().__init__(
            "agent",
            "把只读调查任务委托给拥有独立上下文的子 Agent，并返回总结。",
        )

    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": "需要子 Agent 独立调查的明确任务",
                }
            },
            "required": ["task"],
            "additionalProperties": False,
        }

    def run(self, args: dict, context: ToolContext) -> str:
        task = str(args.get("task", "")).strip()
        if not task:
            return "Error: task 不能为空"
        if context.subagent_runner is None:
            return "Error: sub-agent runner 尚未配置"
        return context.subagent_runner(task)
```

在 `tools/__init__.py` 中导入并注册：

```python
from .agent_tool import AgentTool
```

```python
registry.register(AgentTool())
```

它现在和其他工具一样通过 `ToolRegistry.schemas()` 暴露，通过 `ToolRegistry.execute()` 执行，没有第二套工具分发器。

## 11.5 实现只读子 Agent Loop

创建 `mini_claude/subagent.py`：

```python
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
            elif name not in EXPLORE_TOOLS:
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
```

`schemas` 只包含三个只读工具；执行前又检查一次 `name`，形成双重限制。子 Agent 使用新的 `ToolContext` 和 `messages`，但项目根目录与主 Agent 相同。

## 11.6 把子 Agent 接入主 Agent

在 `agent.py` 顶部增加：

```python
from mini_claude.subagent import run_sub_agent
```

在 `__init__()` 创建完 `self.tools` 和 `self.tool_context` 后增加：

```python
self.tool_context.subagent_runner = lambda task: run_sub_agent(
    task,
    self.client,
    self.model,
    self.tools,
    self.tool_context.project_root,
)
```

主 Agent 不需要在 `chat()` 中特殊判断 `name == "agent"`。模型调用 `agent` 后，现有流程仍然是：

```text
check_permission
→ self._execute_tool
→ ToolRegistry.execute
→ AgentTool.run
→ run_sub_agent
```

## 11.7 更新权限分类

在 `permissions.py` 的 `READ_ONLY_TOOLS` 中加入：

```python
"agent",
```

这里允许的是本章固定的只读探索子 Agent。它不能获得写工具、Shell 或自身的 `agent` 工具。

## 11.8 验证

运行后输入：

```text
请使用子 Agent 调查权限系统是怎么实现的，只告诉我结论和关键文件。
```

确认终端出现 `agent` 工具调用，并验证：

1. 子 Agent 能调用 `read_file`、`list_files`、`grep_search`。
2. 子 Agent 的中间消息没有进入主 Agent 的 `history()`。
3. 主历史中只出现 `agent` 的任务和最终工具结果。
4. 子 Agent 无法调用 `write_file`、`run_shell` 或 `agent`。

## 11.9 理解检查

1. 为什么 `AgentTool` 通过 `ToolContext` 回调启动子 Agent，而不直接导入主 Agent？
2. 子 Agent 为什么需要独立的 `messages` 和 `ToolContext`？
3. 为什么 Schema 过滤和执行前工具名检查需要同时存在？
4. 为什么本章不允许子 Agent 再调用 `agent` 创建下一层子 Agent？
5. 主 Agent 最终应该保存子 Agent 的全部探索历史，还是只保存任务和总结？为什么？

## 11.10 本章最终状态

```text
主 Agent ToolRegistry
→ agent 工具
→ 独立只读子 Agent Loop
→ 三个探索工具
→ 一段总结返回主 Agent
```

主 Agent 原有的 Skills、Plan Mode、Memory、权限和上下文管理都没有分叉。下一章将用同一个 Registry 动态注册 MCP 外部工具。
