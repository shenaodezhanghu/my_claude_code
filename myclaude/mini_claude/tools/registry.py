from __future__ import annotations

from .base import Tool, ToolContext


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

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"工具已经注册：{tool.name}")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def schemas(self) -> list[dict]:
        return [tool.schema() for tool in self._tools.values()]

    def execute(
        self,
        name: str,
        arguments: dict,
        context: ToolContext,
    ) -> str:
        tool = self.get(name)
        if tool is None:
            return f"Error: unknown tool: {name}"

        try:
            result = tool.run(arguments, context)
        except Exception as exc:
            result = f"Error: {type(exc).__name__}: {exc}"

        if not isinstance(result, str):
            result = str(result)
        return result
