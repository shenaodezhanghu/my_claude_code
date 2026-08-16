from __future__ import annotations

import json

from .base import Tool, ToolContext
from .registry import ToolRegistry


class ToolSearchTool(Tool):
    read_only = True
    concurrency_safe = True

    def __init__(self, registry: ToolRegistry) -> None:
        self.registry = registry
        super().__init__(
            "tool_search",
            "按名称或描述搜索尚未加载的延迟工具，并激活匹配工具。",
        )

    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "工具名称或能力关键词；留空列出全部延迟工具",
                }
            },
            "required": ["query"],
            "additionalProperties": False,
        }

    def run(self, args: dict, context: ToolContext) -> str:
        query = str(args.get("query", "")).strip()
        matches = self.registry.search(query)
        activated = self.registry.activate(
            [tool.name for tool in matches]
        )
        if not activated:
            return "No matching deferred tools found."

        payload = [
            {
                "name": tool.name,
                "description": tool.description,
                "schema": tool.schema(),
            }
            for tool in activated
        ]
        return (
            "已激活以下工具；下一轮模型请求会包含它们的 Schema：\n"
            + json.dumps(payload, ensure_ascii=False, indent=2)
        )