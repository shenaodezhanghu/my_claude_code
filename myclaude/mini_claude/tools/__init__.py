from pathlib import Path

from .agent_tool import AgentTool
from .base import ToolContext
from .environment_tools import EnvironmentInfoTool
from .file_tools import (
    EditFileTool,
    GrepSearchTool,
    ListFilesTool,
    ReadFileTool,
    WriteFileTool,
)
from .registry import ToolRegistry
from .search_tool import ToolSearchTool
from .shell_tools import RunShellTool
from .web_tools import WebFetchTool, WebSearchTool


def create_default_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(ReadFileTool())
    registry.register(WriteFileTool())
    registry.register(EditFileTool())
    registry.register(ListFilesTool())
    registry.register(GrepSearchTool())
    registry.register(RunShellTool())
    registry.register(EnvironmentInfoTool())
    registry.register(AgentTool())
    registry.register(WebFetchTool())
    registry.register(WebSearchTool())
    registry.register(ToolSearchTool(registry))
    return registry


def create_tool_context(project_root: Path | None = None) -> ToolContext:
    root = (project_root or Path.cwd()).resolve()
    return ToolContext(project_root=root)


__all__ = [
    "ToolContext",
    "ToolRegistry",
    "create_default_registry",
    "create_tool_context",
]