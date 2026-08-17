import subprocess
import time

from mini_claude.cancellation import AgentCancelled

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
        timeout = float(args.get("timeout", 30))

        if timeout <= 0:
            return "Error: timeout must be greater than 0"

        process = subprocess.Popen(
            args["command"],
            shell=True,
            cwd=context.project_root,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        deadline = time.monotonic() + timeout

        while True:
            # Agent 收到取消信号
            if context.cancelled.is_set():
                process.terminate()

                try:
                    stdout, stderr = process.communicate(
                        timeout=2
                    )
                except subprocess.TimeoutExpired:
                    process.kill()
                    stdout, stderr = process.communicate()

                raise AgentCancelled(
                    "Shell 命令已由用户取消"
                )

            remaining = deadline - time.monotonic()

            # Shell 执行超时
            if remaining <= 0:
                process.kill()
                stdout, stderr = process.communicate()

                return (
                    f"Error: command timed out after "
                    f"{timeout} seconds"
                    f"\nStdout:\n{stdout or ''}"
                    f"\nStderr:\n{stderr or ''}"
                )

            try:
                stdout, stderr = process.communicate(
                    timeout=min(0.2, remaining)
                )
                break
            except subprocess.TimeoutExpired:
                # 每隔最多 0.2 秒重新检查取消信号
                continue

        stdout = stdout or ""
        stderr = stderr or ""
        returncode = process.returncode

        if returncode != 0:
            return (
                f"Command failed (exit code {returncode})"
                f"\nStdout:\n{stdout}"
                f"\nStderr:\n{stderr}"
            )

        return stdout or stderr or "(no output)"
