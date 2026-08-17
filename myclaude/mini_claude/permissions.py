from dataclasses import dataclass
from typing import Literal
import re
from pathlib import Path

from mini_claude.workspace import WorkspacePolicy


PermissionAction = Literal["allow", "deny", "confirm"]
PLAN_BLOCKED_TOOLS = {
    "write_file",
    "edit_file",
    "run_shell",
}
DANGEROUS_COMMANDS = (
    r"\brm\s+-rf\b",
    r"\bdel\s+/[sq]\b",
    r"\brmdir\s+/s\b",
    r"\bgit\s+reset\s+--hard\b",
    r"\bgit\s+push\b.*\s--force\b",
    r"\bformat\s+[a-z]:",
    r"\bshutdown\b",
)
READ_ONLY_TOOLS = {
    "read_file",
    "list_files",
    "grep_search",
    "web_fetch",
    "web_search",
    "environment_info",
    "agent",
    "tool_search",
    "working_memory_read",
    "memory_search",
    "enter_plan_mode",
    "exit_plan_mode",
}
EDIT_TOOLS = {"write_file", "edit_file"}
READ_PATH_TOOLS = {"read_file", "list_files", "grep_search"}
WRITE_PATH_TOOLS = {"write_file", "edit_file"}


@dataclass(frozen=True)
class PermissionResult:
    action: PermissionAction
    message: str = ""


def is_dangerous_command(command: str) -> bool:
    return any(
        re.search(pattern, command, flags=re.IGNORECASE)
        for pattern in DANGEROUS_COMMANDS
    )


def check_permission(
    tool_name: str,
    arguments: dict,
    mode: str = "default",
    agent_mode: str = "default",
    plan_file: str | None = None,
) -> PermissionResult:
    if agent_mode == "plan":
        if tool_name in READ_ONLY_TOOLS:
            return PermissionResult("allow")

        if tool_name in EDIT_TOOLS:
            requested = str(arguments.get("path", "")).replace(
                "\\",
                "/",
            )
            allowed_plan = (plan_file or "").replace("\\", "/")
            if requested == allowed_plan:
                return PermissionResult("allow")
            return PermissionResult(
                "deny",
                f"Plan Mode 只允许修改 {allowed_plan}",
            )

        if tool_name == "run_shell":
            return PermissionResult(
                "deny",
                "Plan Mode 禁止运行 Shell",
            )

        if tool_name.startswith("mcp__"):
            return PermissionResult(
                "deny",
                "Plan Mode 禁止调用行为未知的 MCP 工具",
            )

    if tool_name == "working_memory_update":
        return PermissionResult("allow")

    if tool_name in {"memory_add", "memory_forget"}:
        if mode == "dont_ask":
            return PermissionResult(
                "deny",
                "非交互模式禁止修改长期记忆",
            )
        target = (
            arguments.get("name")
            if tool_name == "memory_forget"
            else arguments.get("candidate_index")
        )
        return PermissionResult(
            "confirm",
            f"{tool_name}: {target}",
        )
    if tool_name in READ_ONLY_TOOLS:
        return PermissionResult("allow")

    if mode == "dont_ask" and tool_name in EDIT_TOOLS:
        return PermissionResult("deny", "非交互模式禁止修改文件")

    if tool_name == "run_shell":
        command = str(arguments.get("command", ""))
        if is_dangerous_command(command):
            if mode == "dont_ask":
                return PermissionResult("deny", f"危险命令：{command}")
            return PermissionResult("confirm", command)
        return PermissionResult("allow")

    if tool_name in EDIT_TOOLS:
        if mode == "accept_edits":
            return PermissionResult("allow")
        return PermissionResult(
            "confirm",
            f"{tool_name}: {arguments.get('path', '')}",
        )

    return PermissionResult("confirm", f"未知权限工具：{tool_name}")


@dataclass(frozen=True)
class PathAccessRequest:
    action: PermissionAction
    message: str = ""
    grant_root: Path | None = None
    access: str = "read"


def check_path_access(
    tool_name: str,
    arguments: dict,
    policy: WorkspacePolicy,
) -> PathAccessRequest:
    if tool_name not in READ_PATH_TOOLS | WRITE_PATH_TOOLS:
        return PathAccessRequest("allow")

    raw_path = str(arguments.get("path") or ".")
    path = policy.resolve_path(raw_path)
    access = (
        "write" if tool_name in WRITE_PATH_TOOLS else "read"
    )
    if policy.is_allowed(path, access):
        return PathAccessRequest("allow")

    grant_root = path if path.is_dir() else path.parent
    return PathAccessRequest(
        "confirm",
        f"允许本会话{access}外部目录：{grant_root}",
        grant_root=grant_root,
        access=access,
    )