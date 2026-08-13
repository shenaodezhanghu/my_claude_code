import subprocess

from .base import Tool, ToolContext


class RunShellTool(Tool):
    def __init__(self) -> None:
        super().__init__(
            "run_shell",
            "在项目根目录执行命令。",
        )

    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "command": {"type": "string"},
                "timeout": {"type": "number", "description": "超时秒数，默认 30"},
            },
            "required": ["command"],
            "additionalProperties": False,
        }

    def run(self, args: dict, context: ToolContext) -> str:
        timeout = args.get("timeout", 30)
        try:
            completed = subprocess.run(
                args["command"],
                shell=True,
                cwd=context.project_root,
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return f"Error: command timed out after {timeout} seconds"

        stdout = completed.stdout or ""
        stderr = completed.stderr or ""
        if completed.returncode != 0:
            return (
                f"Command failed (exit code {completed.returncode})"
                f"\nStdout:\n{stdout}\nStderr:\n{stderr}"
            )
        return stdout or stderr or "(no output)"
