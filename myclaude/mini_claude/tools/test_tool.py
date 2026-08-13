from __future__ import annotations

from typing import Union

from .base import Tool, ToolContext


def calculate_sum(numbers: list[Union[int, float]]) -> Union[int, float]:
    """计算数字列表的总和。

    Args:
        numbers: 包含整数或浮点数的列表

    Returns:
        列表中所有数字的总和
    """
    return sum(numbers)


class CalculateSumTool(Tool):
    """计算一组数字的总和"""

    read_only = True
    concurrency_safe = True

    def __init__(self) -> None:
        super().__init__(
            "calculate_sum",
            "计算一组数字的总和，支持整数和浮点数",
        )

    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "numbers": {
                    "type": "array",
                    "items": {"type": "number"},
                    "description": "要计算总和的数字列表",
                }
            },
            "required": ["numbers"],
            "additionalProperties": False,
        }

    def run(self, args: dict, context: ToolContext) -> str:
        numbers = args.get("numbers", [])
        if not numbers:
            return "Error: 没有提供数字列表"

        try:
            total = calculate_sum(numbers)
            return f"总和: {total}"
        except TypeError as e:
            return f"Error: 列表中包含非数字元素: {e}"
        except Exception as e:
            return f"Error: 计算失败: {e}"
