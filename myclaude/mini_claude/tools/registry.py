from __future__ import annotations

from .base import Tool, ToolContext
from mini_claude.cancellation import AgentCancelled

# MAX_RESULT_CHARS = 50_000


# 截断结果
"""
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
"""

class ToolRegistry:
    """统一管理工具注册、Schema 导出和调用分发。"""

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}
        self._activated: set[str] = set()

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"工具已经注册：{tool.name}")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def all_tools(self) -> list[Tool]:
        return list(self._tools.values())

    def schemas(self) -> list[dict]:
        return [
            tool.schema()
            for tool in self._tools.values()
            if self.is_active(tool.name)
        ]


    def deferred_names(self) -> list[str]:
        return [tool.name for tool in self._tools.values() if tool.deferred and tool.name not in self._activated]

    def search(self, query: str) -> list[Tool]:
        terms = [
            term.lower()
            for term in query.split()
            if term.strip()
        ]
        candidates = [
            tool
            for tool in self._tools.values()
            if tool.deferred and tool.name not in self._activated
        ]
        if not terms:
            return candidates

        matches: list[Tool] = []
        for tool in candidates:
            haystack = f"{tool.name} {tool.description}".lower()
            if all(term in haystack for term in terms):
                matches.append(tool)
        return matches


    def is_active(self, name: str) -> bool:
        tool = self.get(name)
        if tool is None:
            return False
        return not tool.deferred or name in self._activated


    def activate(self, name: list[str]) -> list[Tool]:
        activated: list[Tool] = []
        for tool_name in name:
            tool = self.get(tool_name)
            if tool is None or not tool.deferred:
                continue
            self._activated.add(tool_name)
            activated.append(tool)
        return activated

    def activated_names(self) -> list[str]:
        return sorted(self._activated)

    def restore_activated(self, names: list[str]) -> None:
        valid = {
            name
            for name in names
            if (tool := self.get(name)) is not None and tool.deferred
        }
        self._activated = valid


    def execute(
        self,
        name: str,
        arguments: dict,
        context: ToolContext,
    ) -> str:
        tool = self.get(name)
        if tool is None:
            return f"Error: unknown tool: {name}"
        if not self.is_active(name):
            return (
                f"Error: deferred tool {name!r} 尚未激活，"
                "请先使用 tool_search"
            )
        try:
            result = tool.run(arguments, context)
        except AgentCancelled:
            raise
        except Exception as exc:
            result = f"Error: {type(exc).__name__}: {exc}"

        if not isinstance(result, str):
            result = str(result)
        return result
