from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from collections.abc import Callable


@dataclass
class ToolContext:
    """一次 Agent 会话中由所有工具共享的运行状态。"""

    project_root: Path
    read_file_state: dict[str, float] = field(default_factory=dict)
    subagent_runner: Callable[[str], str] | None = None


class Tool(ABC):

    read_only = False
    concurrency_safe = False
    deferred = False

    def __init__(
        self,
        name: str,
        description: str,
    ):
        self.name = name
        self.description = description


    @abstractmethod
    def parameters(self) -> dict[str, Any]:
        raise NotImplementedError


    @abstractmethod
    def run(self, args: dict, context: ToolContext) -> str:
        raise NotImplementedError


    def schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters(),
            },
        }
