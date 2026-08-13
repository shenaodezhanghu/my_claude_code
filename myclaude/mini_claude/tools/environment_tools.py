import sys
import platform
from datetime import datetime

from .base import Tool, ToolContext


class EnvironmentInfoTool(Tool):

    def __init__(self):
        super().__init__(
            "environment_info",
            "获取当前运行环境和时间信息"
        )


    def parameters(self):
        return {
            "type":"object",
            "properties":{}
        }


    def run(self, args: dict, context: ToolContext) -> str:
        return str({
            "time": datetime.now().isoformat(),
            "python_version": sys.version,
            "platform": platform.platform(),
        })