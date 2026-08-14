# 第一章 用 ToolRegistry 构建最小 Agent Loop

这一章从普通模型调用迈向真正的智能体。为了保证后面的教程不反复推翻前面的代码，我们从第一个工具开始就使用当前项目的统一写法：`Tool + ToolRegistry + ToolContext + tools/`。

本章只注册 `read_file`，用于看清最小闭环；第二章继续在同一个工具包中加入其他七个工具。

## 1.1 本章目标

1. 理解模型调用、工具执行和结果回传组成的 Agent Loop。
2. 从第一件工具开始使用统一的 `Tool` 接口。
3. 使用 `ToolRegistry` 生成工具 Schema 并完成调用分发。
4. 使用 `ToolContext` 约束工具只能访问当前项目。
5. 正确保存 assistant 的 `tool_calls` 和对应的工具结果。

## 1.2 Agent Loop 架构

```text
用户任务
  ↓
messages 加入 user 消息
  ↓
registry.schemas() 提供工具说明
  ↓
调用百炼模型
  ↓
模型是否请求工具？ ── 否 ──→ 输出最终回答
  │
  是
  ↓
registry.execute(name, arguments, context)
  ↓
把 tool 结果加入 messages
  ↓
重新调用模型
```

模型不会亲自读取硬盘。它只生成结构化的工具调用请求，真正访问文件的是本地 Python 工具。

## 1.3 建立统一工具包

在 `myclaude/mycode` 中建立：

```text
mini_claude/
├── __init__.py
├── agent.py
├── model.py
└── tools/
    ├── __init__.py
    ├── base.py
    ├── registry.py
    └── file_tools.py
```

不要再创建 `mini_claude/tools.py`。`tools` 在整套教程中始终是一个文件夹形式的 Python 包。

### 1.3.1 定义 ToolContext 和 Tool

创建 `mini_claude/tools/base.py`：

```python
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class ToolContext:
    project_root: Path
    read_file_state: dict[str, float] = field(default_factory=dict)


class Tool(ABC):
    read_only = False
    concurrency_safe = False

    def __init__(self, name: str, description: str):
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
```

`schema()` 生成给百炼模型看的工具说明，`run()` 是本地真正执行的行为。两者属于同一个工具对象，因此不会出现 Schema 名称和执行函数名称写成两套的问题。

### 1.3.2 定义 ToolRegistry

创建 `mini_claude/tools/registry.py`：

```python
from .base import Tool, ToolContext


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"工具已经注册：{tool.name}")
        self._tools[tool.name] = tool

    def schemas(self) -> list[dict]:
        return [tool.schema() for tool in self._tools.values()]

    def execute(
        self,
        name: str,
        arguments: dict,
        context: ToolContext,
    ) -> str:
        tool = self._tools.get(name)
        if tool is None:
            return f"Error: unknown tool: {name}"

        try:
            result = tool.run(arguments, context)
        except Exception as exc:
            return f"Error: {type(exc).__name__}: {exc}"

        return result if isinstance(result, str) else str(result)
```

Registry 只负责三件事：注册工具、导出 Schema、按照名称执行工具。Agent 不需要知道每个工具内部如何实现。

## 1.4 实现第一个 read_file

创建 `mini_claude/tools/file_tools.py`：

```python
from pathlib import Path

from .base import Tool, ToolContext


def resolve_project_path(raw_path: str, context: ToolContext) -> Path:
    candidate = (context.project_root / raw_path).resolve()
    try:
        candidate.relative_to(context.project_root)
    except ValueError as exc:
        raise ValueError("只允许访问当前项目目录中的文件") from exc
    return candidate


class ReadFileTool(Tool):
    read_only = True
    concurrency_safe = True

    def __init__(self) -> None:
        super().__init__(
            name="read_file",
            description="读取当前项目目录中的 UTF-8 文本文件",
        )

    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "相对于项目根目录的文件路径",
                }
            },
            "required": ["path"],
            "additionalProperties": False,
        }

    def run(self, args: dict, context: ToolContext) -> str:
        path = resolve_project_path(args["path"], context)
        if not path.exists():
            return f"Error: 文件不存在：{args['path']}"
        if not path.is_file():
            return f"Error: 不是文件：{args['path']}"

        content = path.read_text(encoding="utf-8")
        context.read_file_state[str(path)] = path.stat().st_mtime
        return content
```

这里已经包含两个必要保护：文件不存在时返回友好提示；解析后的路径必须位于 `project_root` 内，`../` 不能逃出项目目录。

## 1.5 创建默认 Registry

创建 `mini_claude/tools/__init__.py`：

```python
from pathlib import Path

from .base import ToolContext
from .file_tools import ReadFileTool
from .registry import ToolRegistry


def create_default_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(ReadFileTool())
    return registry


def create_tool_context(project_root: Path | None = None) -> ToolContext:
    return ToolContext(project_root=(project_root or Path.cwd()).resolve())
```

第一章的默认 Registry 只有 `read_file`。第二章只需要继续注册新工具，不需要改变 Agent Loop。

## 1.6 实现 Agent Loop

创建 `mini_claude/agent.py`：

```python
import json

from mini_claude.model import create_client, get_models
from mini_claude.tools import create_default_registry, create_tool_context


SYSTEM_PROMPT = "你是编程助手。涉及项目文件内容时，必须先使用工具读取。"


class MINI_CLUE_AGENT:
    def __init__(self) -> None:
        self.client = create_client()
        self.model = get_models()
        self.messages: list[dict] = []
        self.tools = create_default_registry()
        self.tool_context = create_tool_context()

    def chat(self, user_text: str) -> str:
        self.messages.append({"role": "user", "content": user_text})

        while True:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    *self.messages,
                ],
                tools=self.tools.schemas(),
            )
            message = response.choices[0].message
            self.messages.append(message.model_dump(exclude_none=True))

            if not message.tool_calls:
                return message.content or ""

            for tool_call in message.tool_calls:
                name = tool_call.function.name
                arguments = json.loads(tool_call.function.arguments)
                print(f"-> {name}: {arguments}")

                result = self.tools.execute(
                    name,
                    arguments,
                    self.tool_context,
                )
                self.messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": result,
                    }
                )
```

`self.tools` 和 `self.tool_context` 都只创建一次。后者以后还要保存 read-before-edit 的读取时间；如果每次调用工具都重新创建，状态会丢失。

assistant 消息必须连同 `tool_calls` 原样加入历史，工具结果则通过相同的 `tool_call_id` 与请求配对。

## 1.7 使用当前 REPL 入口

`main.py` 使用你当前已经实现的连续对话写法：

```python
from dotenv import load_dotenv

from mini_claude.agent import MINI_CLUE_AGENT


load_dotenv()


def main() -> None:
    agent = MINI_CLUE_AGENT()
    print("Mini Agent 已启动。输入 exit 退出。")

    while True:
        try:
            prompt = input("\n你：").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n再见！")
            break

        if not prompt:
            continue
        if prompt.lower() in {"exit", "quit"}:
            print("再见！")
            break

        answer = agent.chat(prompt)
        print(f"助手：{answer}")


if __name__ == "__main__":
    main()
```

外层 `while True` 等待多个用户问题，`chat()` 内层 `while True` 完成一次任务中的多轮模型—工具调用。Agent 必须创建在外层循环之前，否则每次输入都会丢失消息历史。

## 1.8 运行验证

在 `myclaude/mycode/demo.txt` 中写入：

```text
项目代号是 BANANA-42。
```

将工作目录设为 `myclaude/mycode` 后运行：

```bat
python main.py
```

输入：

```text
读取 demo.txt，告诉我项目代号
```

预期过程：

```text
-> read_file: {'path': 'demo.txt'}
助手：项目代号是 BANANA-42。
```

再测试越界限制：

```text
读取 ../../../README.md
```

工具应返回只允许访问当前项目目录的错误。

## 1.9 理解检查

完成本章后，尝试不看代码回答：

1. 为什么必须先保存带 `tool_calls` 的 assistant 消息，再保存对应的 tool 消息？
        因为需要告诉模型这个结果，是你刚才发起的哪个工具调用返回的。
2. 为什么 `function.arguments` 是 JSON 字符串，而工具的 `run()` 需要字典？
        因为模型和程序之间传输适合用 JSON 文本,工具需要输入参数，参数在传输的字典里提取方便
3. 如果模型连续调用两次工具，`while True` 中的消息顺序会是什么？串行调用
        飞洒发生
4. 为什么工具应该通过 Registry 查找，而不是在 Agent Loop 中写大量 `if name == ...`？ 
        因为工具一多，Agent Loop 会越来越乱。使用 Registry的话Agent 根本不关心具体是什么工具。
5. `System Prompt` 和工具 Schema 分别解决“什么时候调用”与“怎样调用”中的哪一部分？

## 1.10 本章小结

本章完成了最小 Agent Loop，而且从第一件工具开始便使用最终会持续扩展的 Registry 写法：

```text
ReadFileTool
    ↓ register
ToolRegistry
    ↓ schemas / execute
MINI_CLUE_AGENT
    ↓ tool result
百炼模型继续推理
```

第二章将在这个结构中补齐写入、编辑、文件列表、代码搜索、Shell、网页读取和联网搜索，不会再更换工具架构。
