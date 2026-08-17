from __future__ import annotations

from .base import Tool, ToolContext


class EnterPlanModeTool(Tool):
    read_only = True
    deferred = False

    def __init__(self) -> None:
        super().__init__(
            "enter_plan_mode",
            "进入只读规划模式，并创建本会话的 Plan 文件。",
        )

    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        }

    def run(self, args: dict, context: ToolContext) -> str:
        if context.enter_plan_runner is None:
            return "Error: enter plan runner 尚未配置"
        return context.enter_plan_runner()


class ExitPlanModeTool(Tool):
    read_only = True
    deferred = False

    def __init__(self) -> None:
        super().__init__(
            "exit_plan_mode",
            "Plan 文件完成后提交给用户审批。",
        )

    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        }

    def run(self, args: dict, context: ToolContext) -> str:
        if context.exit_plan_runner is None:
            return "Error: exit plan runner 尚未配置"
        return context.exit_plan_runner()