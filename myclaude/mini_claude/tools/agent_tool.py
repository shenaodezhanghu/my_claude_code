from __future__ import annotations

from .base import Tool, ToolContext


class AgentTool(Tool):
    read_only = True
    deferred = True

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