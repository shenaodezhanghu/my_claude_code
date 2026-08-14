# 第二章 构建可扩展的工具系统

第一章已经建立 `Tool + ToolRegistry + ToolContext + tools/`，并注册了第一个 `read_file`。本章不更换架构，只在相同接口上补齐你已经实现的文件、搜索、Shell 和网络工具。

本章只注册你已经实现的八个工具：

```text
read_file
write_file
edit_file
list_files
grep_search
run_shell
web_fetch
web_search
```

尚未实现的 Deferred Tools、Plan Mode、Skills、MCP、工具权限、统计和评测不写入代码。它们只会在对应章节、确实产生价值时再接入。

## 2.1 本章目标

完成本章后，你将能够：

1. 理解工具名称、Schema 和执行函数之间的关系。
2. 使用统一 `Tool` 抽象开发工具。
3. 使用 `ToolRegistry` 注册、导出和执行工具。
4. 使用 `ToolContext` 保存项目根目录和 read-before-edit 状态。
5. 实现文件、搜索、Shell、WebFetch 和 WebSearch 工具。
6. 使用 mtime 防止覆盖用户刚修改的文件。
7. 限制大结果，避免工具输出挤满模型上下文。

## 2.2 扩展第一章的工具包

第一章已有：

```text
mini_claude/
├── agent.py
└── tools/
    ├── __init__.py
    ├── base.py
    ├── registry.py
    └── file_tools.py
```

本章在同一目录中补充分类模块：

```text
mini_claude/
├── agent.py
└── tools/
    ├── __init__.py
    ├── base.py
    ├── registry.py
    ├── file_tools.py
    ├── shell_tools.py
    └── web_tools.py
```

各文件职责如下：

| 文件 | 职责 |
|---|---|
| `base.py` | 定义统一的 `Tool` 接口和共享 `ToolContext` |
| `registry.py` | 注册工具、导出 Schema、按名称执行、截断结果 |
| `file_tools.py` | 文件读取、写入、编辑、列文件和代码搜索 |
| `shell_tools.py` | 命令执行 |
| `web_tools.py` | 读取明确 URL 和 Tavily 关键词搜索 |
| `__init__.py` | 创建项目默认工具注册表 |

整套教程始终以 `mini_claude/tools/` 为唯一工具入口，不创建同名的 `mini_claude/tools.py`。

## 2.3 工具系统完整数据流

```text
create_default_registry()
    ↓ 注册 Tool 对象
ToolRegistry.schemas()
    ↓ 生成 OpenAI Tool Schema
百炼模型返回 tool_calls
    ↓ name + JSON arguments
ToolRegistry.execute()
    ↓ 根据名称找到 Tool
Tool.run(arguments, context)
    ↓ 执行本地能力
结果截断
    ↓
role=tool 消息返回模型
```

每个 `Tool` 同时携带名称、说明、参数 Schema 和执行逻辑；`ToolRegistry` 保存这些对象。因此新增工具只需要定义类并注册一次，不需要维护第二份分发表。

## 2.4 复核并扩展 ToolContext

第一章已经创建 `ToolContext`。本节给出第二章所依赖的完整版本；如果你的文件已经一致，不要重复创建或覆盖。

创建 `mini_claude/tools/base.py`：

```python
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class ToolContext:
    """一次 Agent 会话中由所有工具共享的运行状态。"""

    project_root: Path
    read_file_state: dict[str, float] = field(default_factory=dict)
```

`ToolContext` 当前只保存两个必要状态：

- `project_root`：限制工具只能在当前项目中操作；
- `read_file_state`：记录文件上次成功读取时的 mtime。

这些状态属于一次 Agent 会话，而不是某个独立函数，因此不能放在函数内部每次重新创建。

## 2.5 复核统一 Tool 接口

`Tool` 接口从第一章起保持不变。本节用于确认后续七个工具都遵循相同契约。

继续在 `base.py` 中添加：

```python
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

每个工具必须回答四个问题：

```text
叫什么？            name
用来做什么？        description
需要什么参数？      parameters()
如何真正执行？      run()
```

`schema()` 将工具对象转换成百炼 OpenAI 兼容接口需要的格式。以后新增工具仍然沿用这一条生成路径。

### 安全默认值

```python
read_only = False
concurrency_safe = False
```

新工具默认被视为可能修改状态、不能并发。只有明确证明只读且没有副作用的工具才覆盖为 `True`。这样即使开发者忘记标记，最多运行慢一点，不会把写工具错误地并发执行。

本章只保存这些元数据，尚未实现并发调度；第五章流式输出时再使用。

## 2.6 完善 ToolRegistry

在第一章的 Registry 上增加结果截断，防止文件或命令输出一次占满模型上下文；注册和执行接口保持不变。

创建 `mini_claude/tools/registry.py`：

```python
from .base import Tool, ToolContext


MAX_RESULT_CHARS = 50_000


def truncate_result(result: str) -> str:
    if len(result) <= MAX_RESULT_CHARS:
        return result

    keep_each = (MAX_RESULT_CHARS - 80) // 2
    omitted = len(result) - keep_each * 2
    return (
        result[:keep_each]
        + f"\n\n[... truncated {omitted} characters ...]\n\n"
        + result[-keep_each:]
    )


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"工具已经注册：{tool.name}")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def schemas(self) -> list[dict]:
        return [tool.schema() for tool in self._tools.values()]

    def execute(
        self,
        name: str,
        arguments: dict,
        context: ToolContext,
    ) -> str:
        tool = self.get(name)
        if tool is None:
            return f"Error: unknown tool: {name}"

        try:
            result = tool.run(arguments, context)
        except Exception as exc:
            result = f"Error: {type(exc).__name__}: {exc}"

        if not isinstance(result, str):
            result = str(result)
        return truncate_result(result)
```

注册表只负责管理和调度，不负责实现具体文件或网络业务。

### 为什么错误要返回模型

工具找不到、路径错误或正则无效，都是模型有机会修正的运行信息。如果直接抛出异常，Agent Loop 会终止；转换成字符串后，模型可以重新选择工具或修改参数。

### 为什么截断保留头尾

编译和测试输出的主要过程通常在开头，错误摘要与统计经常位于结尾，因此保留头尾比只保留前 50,000 字符更有用。

## 2.7 项目路径限制

在 `file_tools.py` 中实现公共路径解析：

```python
from pathlib import Path

from .base import Tool, ToolContext


def resolve_project_path(raw_path: str, context: ToolContext) -> Path:
    project_root = context.project_root.resolve()
    path = (project_root / raw_path).resolve()

    if not path.is_relative_to(project_root):
        raise PermissionError("只能访问当前项目目录中的文件")

    return path
```

先 `resolve()` 再检查，可以阻止：

```text
../README.md
C:\Users\...\secret.txt
项目内符号链接最终指向项目外
```

所有文件工具必须使用这个公共函数，不能有的工具限制路径、有的工具直接使用 `Path(args["path"])`。

## 2.8 ReadFileTool

```python
class ReadFileTool(Tool):
    read_only = True
    concurrency_safe = True

    def __init__(self) -> None:
        super().__init__(
            "read_file",
            "读取当前项目中的 UTF-8 文本文件，返回带行号的内容",
        )

    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "相对于项目根目录的路径",
                }
            },
            "required": ["path"],
            "additionalProperties": False,
        }

    def run(self, args: dict, context: ToolContext) -> str:
        raw_path = args.get("path", "").strip()
        if not raw_path:
            return "Error: 没有提供文件路径"

        try:
            path = resolve_project_path(raw_path, context)
            content = path.read_text(encoding="utf-8")

            # read-before-edit：记录本次看到的文件版本。
            context.read_file_state[str(path)] = path.stat().st_mtime

            return "\n".join(
                f"{number:4d} | {line}"
                for number, line in enumerate(content.splitlines(), 1)
            )
        except FileNotFoundError:
            return f"Error: 文件不存在：{raw_path}"
        except IsADirectoryError:
            return f"Error: 目标是目录而不是文件：{raw_path}"
        except UnicodeDecodeError:
            return f"Error: 文件不是有效的 UTF-8 文本：{raw_path}"
        except PermissionError as exc:
            return f"Error: {exc}"
        except OSError as exc:
            return f"Error: 读取失败：{exc}"
```

行号只用于模型定位；真正编辑时，`old_text` 不包含显示行号。

## 2.9 read-before-edit 与 mtime

公共检查函数：

```python
def check_file_freshness(
    path: Path,
    context: ToolContext,
) -> str | None:
    # 创建新文件不需要先读。
    if not path.exists():
        return None

    recorded_mtime = context.read_file_state.get(str(path))
    if recorded_mtime is None:
        return "Error: 修改已有文件前必须先使用 read_file 读取当前内容"

    if path.stat().st_mtime != recorded_mtime:
        return (
            "Warning: 文件在上次读取后已被外部修改。"
            "请重新调用 read_file，再根据最新内容修改"
        )

    return None
```

工作过程：

```text
read_file(app.py)
→ 记录 mtime = 1000

用户在 PyCharm 保存 app.py
→ mtime = 1005

edit_file(app.py)
→ 1005 != 1000
→ 拒绝覆盖，要求重新读取
```

成功写入或编辑后也要更新 `read_file_state`，否则 Agent 会把自己刚完成的修改误认为外部修改。

## 2.10 WriteFileTool

`WriteFileTool` 的关键流程是：解析安全路径、检查已有文件版本、创建父目录、写入并更新 mtime。

```python
class WriteFileTool(Tool):
    def __init__(self) -> None:
        super().__init__(
            "write_file",
            "创建或覆盖文件；覆盖已有文件前必须先 read_file",
        )

    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["path", "content"],
            "additionalProperties": False,
        }

    def run(self, args: dict, context: ToolContext) -> str:
        path = resolve_project_path(args["path"], context)
        freshness_error = check_file_freshness(path, context)
        if freshness_error:
            return freshness_error

        content = args["content"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        context.read_file_state[str(path)] = path.stat().st_mtime

        lines = content.splitlines()
        preview = "\n".join(
            f"{number:4d} | {line}"
            for number, line in enumerate(lines[:30], 1)
        )
        omitted = f"\n... ({len(lines)} lines total)" if len(lines) > 30 else ""
        return (
            f"Successfully wrote {path.relative_to(context.project_root)} "
            f"({len(lines)} lines)\n\n{preview}{omitted}"
        )
```

只返回“成功”会让模型和用户无法确认具体写入内容，因此附带最多 30 行预览。

## 2.11 EditFileTool

编辑工具使用精确字符串替换，而不是行号或全文件重写。

```text
old_text 不存在
→ 模型记忆与文件不一致，安全失败

old_text 出现多次
→ 不能确定修改位置，要求提供更多上下文

old_text 唯一
→ 替换一次并返回 Diff
```

### 引号容错

模型可能把直引号转换成弯引号：

```python
def normalize_quotes(text: str) -> str:
    return (
        text.replace("‘", "'").replace("’", "'").replace("′", "'")
        .replace("“", '"').replace("”", '"').replace("″", '"')
    )
```

先尝试严格匹配，失败后只对引号进行标准化匹配。命中后必须返回文件中的原始文本，而不是标准化版本，避免顺便改变文件风格。

### Diff 输出

编辑成功后返回：

```diff
@@ -15,1 +15,1 @@
- const message = "hello"
+ const message = "world"
```

Diff 是可观察证据，不是二次应用的补丁。

完整实现位于：

[file_tools.py](<E:/研究生/学习/ai_study/claude-code/claude-code-from-scratch/myclaude/mycode/mini_claude/tools/file_tools.py>)

## 2.12 ListFilesTool 与 GrepSearchTool

两者都属于只读、并发安全工具：

```python
class ListFilesTool(Tool):
    read_only = True
    concurrency_safe = True


class GrepSearchTool(Tool):
    read_only = True
    concurrency_safe = True
```

`list_files` 使用 glob 模式找文件：

```text
**/*.py
mini_claude/**/*.py
```

它忽略 `.git` 和 `node_modules`，最多返回 200 个结果，并说明省略数量。

`grep_search` 使用正则搜索内容：

```text
pattern="ToolRegistry"
path="mini_claude"
include="*.py"
```

它返回：

```text
mini_claude/tools/registry.py:21: class ToolRegistry:
```

正则非法时返回工具错误，不让 Agent Loop 崩溃；结果最多保留 100 条，并统计其余匹配数。

## 2.13 RunShellTool

`run_shell` 执行命令并返回 stdout、stderr 和退出码：

```python
class RunShellTool(Tool):
    def run(self, args: dict, context: ToolContext) -> str:
        completed = subprocess.run(
            args["command"],
            shell=True,
            cwd=context.project_root,
            text=True,
            capture_output=True,
            timeout=args.get("timeout", 30),
        )
        ...
```

成功但没有输出时返回 `(no output)`，避免模型误以为工具没有执行。失败时 stdout 和 stderr 都要保留，因为编译器或测试框架可能把有用信息分散在两个输出流中。

当前只实现超时，没有实现权限和危险命令拦截。第六章再添加，不要在本章假装它已经安全。

## 2.14 WebFetchTool

`web_fetch` 用于读取已知 URL：

```text
web_fetch(url="https://example.com")
```

它不是关键词搜索。实现时必须：

- 只允许 HTTP/HTTPS，拒绝 `file://`；
- 设置 30 秒超时；
- 删除 HTML 中的 `script`、`style` 和标签；
- 解码 HTML 实体；
- 限制最大字符数；
- 把网络错误返回模型。

完整实现位于：

[web_tools.py](<E:/研究生/学习/ai_study/claude-code/claude-code-from-scratch/myclaude/mycode/mini_claude/tools/web_tools.py>)

## 2.15 WebSearchTool

你已经使用 Tavily 实现了关键词搜索，因此将它保留为独立工具：

```python
class WebSearchTool(Tool):
    read_only = True
    concurrency_safe = True

    def run(self, args: dict, context: ToolContext) -> str:
        import tavily
        response = tavily.search(
            query=args["query"],
            max_results=5,
        )
        return json.dumps(
            response,
            ensure_ascii=False,
            default=str,
        )
```

两种网页工具分工如下：

```text
web_search(query)
→ 根据关键词寻找可能相关的网页

web_fetch(url)
→ 获取某个明确 URL 的正文
```

使用前安装并配置 Tavily：

```bat
pip install tavily-python
```

API Key 的具体变量名应以你安装的 Tavily SDK 版本为准。缺少依赖时工具返回友好错误，不影响其他工具启动。

## 2.16 创建默认注册表

在 `tools/__init__.py` 中统一注册当前已有工具：

```python
from pathlib import Path

from .base import ToolContext
from .file_tools import (
    EditFileTool,
    GrepSearchTool,
    ListFilesTool,
    ReadFileTool,
    WriteFileTool,
)
from .registry import ToolRegistry
from .shell_tools import RunShellTool
from .web_tools import WebFetchTool, WebSearchTool


def create_default_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(ReadFileTool())
    registry.register(WriteFileTool())
    registry.register(EditFileTool())
    registry.register(ListFilesTool())
    registry.register(GrepSearchTool())
    registry.register(RunShellTool())
    registry.register(WebFetchTool())
    registry.register(WebSearchTool())
    return registry


def create_tool_context(project_root: Path | None = None) -> ToolContext:
    return ToolContext(
        project_root=(project_root or Path.cwd()).resolve()
    )
```

这就是当前项目的组合根：哪些工具属于默认 Agent，在这里一眼可见。

## 2.17 Agent 接入 ToolRegistry

修改 `agent.py` 的导入：

```python
from mini_claude.tools import (
    create_default_registry,
    create_tool_context,
)
```

在构造函数中只创建一次：

```python
class MINI_CLUE_AGENT:
    def __init__(self) -> None:
        self.client = create_client()
        self.model = get_models()
        self.messages: list[dict] = []

        self.tools = create_default_registry()
        self.tool_context = create_tool_context()
```

API 请求使用注册表生成 Schema：

```python
response = self.client.chat.completions.create(
    model=self.model,
    messages=[
        {"role": "system", "content": SYSTEM_PROMPT},
        *self.messages,
    ],
    tools=self.tools.schemas(),
)
```

执行工具：

```python
result = self.tools.execute(
    name,
    arguments,
    self.tool_context,
)
```

Agent 不再知道每个工具的实现细节，只依赖 Registry 的两个稳定接口：

```text
schemas()
execute()
```

## 2.18 当前不实现的内容

以下能力不属于你当前已经完成的代码，本章不添加实现：

| 能力 | 后续处理 |
|---|---|
| Deferred Tools / `tool_search` | 工具数量和低频工具确实需要时再实现 |
| 工具权限 | 第六章 |
| Skills | 第九章 |
| Plan Mode 工具 | 第十章 |
| Sub-Agent 工具 | 第十一章 |
| MCP 动态工具 | 第十二章 |
| 工具统计、Trace、评测 | 主线完成后评估 |

`ToolRegistry` 为这些能力提供扩展点，但扩展点不等于已经实现。教程必须明确区分“现在可运行”和“未来可以加入”。

## 2.19 验证

先进行纯本地验证，不调用百炼：

```python
from pathlib import Path

from mini_claude.tools import (
    create_default_registry,
    create_tool_context,
)


registry = create_default_registry()
context = create_tool_context(Path.cwd())

print([
    schema["function"]["name"]
    for schema in registry.schemas()
])

print(registry.execute(
    "read_file",
    {"path": "demo.txt"},
    context,
))
```

预期工具列表：

```text
read_file
write_file
edit_file
list_files
grep_search
run_shell
web_fetch
web_search
```

再运行 Agent：

```bat
python main.py
```

依次测试：

```text
读取 demo.txt
搜索当前项目中 MINI_CLAUDE_MODEL 出现在哪里
创建 notes/test.txt
先读取 notes/test.txt，再修改其中内容
读取 https://example.com
搜索 Python Agent Loop 的资料
```

### 验证 read-before-edit

在一个新 Agent 会话中直接修改已有文件，应收到必须先读取的错误。

### 验证 mtime

1. 让 Agent 读取文件；
2. 在 PyCharm 中修改并保存；
3. 让 Agent 再编辑；
4. 工具应要求重新读取。

## 2.20 理解检查

1. 为什么同一次会话中的所有文件工具必须共享同一个 `ToolContext`？
   因为共享 ToolContext 是为了让工具之间共享记忆，保证在edit的时候读取
2. `read-before-edit` 为什么既需要目标路径，又需要文件修改时间？
3. 为什么工具异常应转换为字符串返回模型，而不是直接结束进程？
   把异常返回模型，是为了让模型自己恢复、重试或换方案。
4. 为什么结构化的 WebSearch 结果必须先规范化成可序列化文本？
   因为 WebSearch 返回的可能不是普通字符串，而 API 消息通常需要可以转成 JSON 的内容，就是把各种复杂对象统一变成模型能稳定接收的文本。
5. `read_only`、`concurrency_safe` 和权限策略分别表达什么不同信息？
   read_only 描述工具的副作用，concurrency_safe 描述并发安全性，权限策略决定“这次到底能不能执行”。

## 2.21 本章小结

本章沿用第一章建立的最小工具框架，并在其中补齐现有工具：

```text
Tool
→ 每个工具的统一契约

ToolRegistry
→ 注册、Schema 导出和执行入口

ToolContext
→ 项目边界和会话级文件状态

分类工具模块
→ 文件、Shell 和 Web 能力各自维护
```

这套连续写法保留了 `claude-code-from-scratch` 的底层工具回路，同时借鉴了 HelloAgents 的工具注册思想。第一章建立接口，第二章只扩展实现，没有中途更换工具架构，也没有提前加入尚未实现的复杂功能。

下一章再完成 `prompt.py`，把当前内联的简单 System Prompt 升级为静态核心与动态环境组合。
