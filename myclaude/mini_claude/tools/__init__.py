import threading
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
from .memory_tools import (
    MemoryAddTool,
    MemoryForgetTool,
    MemorySearchTool,
    WorkingMemoryReadTool,
    WorkingMemoryUpdateTool,
)
from ..session_workspace import SessionWorkspace
from .plan_tools import EnterPlanModeTool, ExitPlanModeTool
from ..workspace import WorkspacePolicy


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
    registry.register(EnterPlanModeTool())
    registry.register(ExitPlanModeTool())
    registry.register(ToolSearchTool(registry))
    registry.register(WorkingMemoryReadTool())
    registry.register(WorkingMemoryUpdateTool())
    registry.register(MemorySearchTool())
    registry.register(MemoryAddTool())
    registry.register(MemoryForgetTool())
    return registry



def create_tool_context(
    project_root: Path | None = None,
    session_workspace: SessionWorkspace | None = None,
    workspace_policy: WorkspacePolicy | None = None,
    cancelled: threading.Event | None = None,
) -> ToolContext:
    root = (project_root or Path.cwd()).resolve()
    policy = workspace_policy or WorkspacePolicy(root)
    return ToolContext(
        project_root=policy.workspace_root,
        session_workspace=session_workspace,
        workspace_policy=policy,
        cancelled=cancelled or threading.Event(),
    )


__all__ = [
    "ToolContext",
    "ToolRegistry",
    "create_default_registry",
    "create_tool_context",
]