from pathlib import Path

from .base import ToolContext
from .file_tools import (
    EditFileTool,
    GrepSearchTool,
    ListFilesTool,
    ReadFileTool,
    WriteFileTool,

)
from .registry import ToolRegistry
from .shell_tools import RunShellTool
from .web_tools import WebFetchTool, WebSearchTool
from .environment_tools import EnvironmentInfoTool


def create_default_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(ReadFileTool())
    registry.register(WriteFileTool())
    registry.register(EditFileTool())
    registry.register(ListFilesTool())
    registry.register(GrepSearchTool())
    registry.register(RunShellTool())
    registry.register(WebFetchTool())
    registry.register(WebSearchTool())
    registry.register(EnvironmentInfoTool())
    return registry


def create_tool_context(project_root: Path | None = None) -> ToolContext:
    return ToolContext(project_root=(project_root or Path.cwd()).resolve())


__all__ = [
    "ToolContext",
    "ToolRegistry",
    "create_default_registry",
    "create_tool_context",
]
