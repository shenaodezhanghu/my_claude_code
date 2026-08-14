from dataclasses import dataclass
from typing import Literal
import re

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
}
EDIT_TOOLS = {"write_file", "edit_file"}


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
) -> PermissionResult:
    if agent_mode == "plan" and tool_name in PLAN_BLOCKED_TOOLS:
        return PermissionResult(
            "deny",
            f"Plan Mode 禁止执行 {tool_name}",
        )
    if agent_mode == "plan" and tool_name.startswith("mcp__"):
        return PermissionResult(
            "deny",
            "Plan Mode 禁止调用行为未知的 MCP 外部工具",
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