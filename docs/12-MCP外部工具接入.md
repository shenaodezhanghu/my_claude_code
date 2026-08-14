# 第十二章 MCP 外部工具接入

> 本章从第十一章最终代码继续。MCP 工具不会建立另一套 Agent 工具循环；连接服务器后，每个外部工具都会被包装成 `Tool` 并注册进现有 `ToolRegistry`。

## 12.1 MCP 解决什么问题

当前工具都写在项目源码中。MCP 允许 Agent 通过标准协议连接外部进程，动态发现并调用工具：

```text
启动 MCP Server 子进程
→ JSON-RPC initialize
→ tools/list
→ 包装成 mcp__服务器__工具名
→ 注册进 ToolRegistry
→ 模型正常调用
→ tools/call 转发给服务器
```

本章按照原教程实现最小 stdio MCP 客户端：一个服务器、同步 JSON-RPC、首次聊天时连接。

这里需要区分“协议统一”和“服务器配置统一”：MCP 统一的是握手、工具发现和工具调用格式；不同服务器仍然需要各自的启动命令、参数和认证信息。就像 HTTP 的请求格式统一，但不同网站仍有不同地址。Agent 不应该为 GitHub、Filesystem 等服务器分别编写 `if`，而应该读取一份通用启动配置。

## 12.2 本章结构

```text
mini_claude/
├── agent.py
├── mcp_client.py             # 本章新增
└── tools/
    ├── mcp_tool.py           # 本章新增
    └── registry.py           # 继续使用现有 Registry
```

## 12.3 实现最小 MCP 客户端

创建 `mini_claude/mcp_client.py`：

```python
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import json
import os
import re
import subprocess
import threading


@dataclass(frozen=True)
class McpServerConfig:
    name: str
    command: str
    args: list[str]


def load_mcp_config(
    environ: Mapping[str, str] | None = None,
) -> McpServerConfig | None:
    source = os.environ if environ is None else environ
    command = source.get("MINI_MCP_COMMAND", "").strip()
    if not command:
        return None

    name = source.get("MINI_MCP_NAME", "external").strip() or "external"
    if re.fullmatch(r"[A-Za-z0-9_-]+", name) is None:
        raise ValueError(
            "MINI_MCP_NAME 只能包含字母、数字、下划线和连字符"
        )

    raw_args = source.get("MINI_MCP_ARGS", "[]")
    try:
        args = json.loads(raw_args)
    except json.JSONDecodeError as exc:
        raise ValueError("MINI_MCP_ARGS 必须是 JSON 字符串数组") from exc
    if not isinstance(args, list) or not all(
        isinstance(item, str) for item in args
    ):
        raise ValueError("MINI_MCP_ARGS 必须是 JSON 字符串数组")

    return McpServerConfig(name=name, command=command, args=args)


class McpConnection:
    def __init__(self, command: str, args: list[str]) -> None:
        self.proc = subprocess.Popen(
            [command, *args],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        self._id = 0
        self._lock = threading.Lock()
        self.tools: list[dict] = []

    def _request(self, method: str, params: dict | None = None) -> dict:
        with self._lock:
            self._id += 1
            request_id = self._id
            request = {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": method,
                "params": params or {},
            }

            assert self.proc.stdin is not None
            assert self.proc.stdout is not None
            self.proc.stdin.write(json.dumps(request) + "\n")
            self.proc.stdin.flush()

            while True:
                line = self.proc.stdout.readline()
                if not line:
                    raise RuntimeError("MCP Server 已停止")
                try:
                    response = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if response.get("id") == request_id:
                    if "error" in response:
                        raise RuntimeError(
                            f"MCP error: {response['error']}"
                        )
                    return response

    def _notify(self, method: str) -> None:
        assert self.proc.stdin is not None
        self.proc.stdin.write(
            json.dumps({"jsonrpc": "2.0", "method": method}) + "\n"
        )
        self.proc.stdin.flush()

    def connect(self) -> "McpConnection":
        self._request(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {
                    "name": "mini-claude",
                    "version": "1.0",
                },
            },
        )
        self._notify("notifications/initialized")
        listed = self._request("tools/list")
        self.tools = listed.get("result", {}).get("tools", [])
        return self

    def call_tool(self, name: str, arguments: dict) -> str:
        response = self._request(
            "tools/call",
            {"name": name, "arguments": arguments},
        )
        result = response.get("result", {})
        content = result.get("content", [])
        text = "\n".join(
            item.get("text", "")
            for item in content
            if item.get("type") == "text"
        )
        return text or json.dumps(result, ensure_ascii=False)

    def close(self) -> None:
        if self.proc.poll() is None:
            self.proc.terminate()


def connect_mcp(command: str, args: list[str]) -> McpConnection:
    return McpConnection(command, args).connect()
```

`MINI_MCP_COMMAND` 保存可执行程序，`MINI_MCP_ARGS` 使用 JSON 字符串数组保存参数，`MINI_MCP_NAME` 只负责生成工具名前缀。服务器不同只改变这三个配置，不改变 Agent 代码。

通信顺序是固定的：`initialize` → `notifications/initialized` → `tools/list`。不能跳过握手直接调用工具。

## 12.4 把 MCP 工具适配成 Tool

创建 `mini_claude/tools/mcp_tool.py`：

```python
from __future__ import annotations

from mini_claude.mcp_client import McpConnection

from .base import Tool, ToolContext


class McpProxyTool(Tool):
    def __init__(
        self,
        server_name: str,
        definition: dict,
        connection: McpConnection,
    ) -> None:
        self.server_name = server_name
        self.remote_name = str(definition["name"])
        self.definition = definition
        self.connection = connection
        super().__init__(
            f"mcp__{server_name}__{self.remote_name}",
            str(definition.get("description") or "MCP external tool"),
        )

    def parameters(self) -> dict:
        return self.definition.get("inputSchema") or {
            "type": "object",
            "properties": {},
        }

    def run(self, args: dict, context: ToolContext) -> str:
        return self.connection.call_tool(self.remote_name, args)
```

例如，GitHub 服务器的 `search_repositories` 会注册为 `mcp__github__search_repositories`。这个前缀同时避免与本地工具重名，并保留服务器和远程工具名。

## 12.5 在 Agent 首次使用时连接

在 `agent.py` 顶部增加：

```python
from mini_claude.mcp_client import (
    McpConnection,
    connect_mcp,
    load_mcp_config,
)
from mini_claude.tools.mcp_tool import McpProxyTool
```

在 `__init__()` 中增加：

```python
self.mcp: McpConnection | None = None
self.mcp_attempted = False
```

增加连接方法：

```python
def _ensure_mcp(self) -> None:
    if self.mcp_attempted:
        return
    self.mcp_attempted = True

    try:
        config = load_mcp_config()
        if config is None:
            return

        connection = connect_mcp(config.command, config.args)
        for definition in connection.tools:
            self.tools.register(
                McpProxyTool(config.name, definition, connection)
            )
        self.mcp = connection
        print(
            f"已连接 MCP Server {config.name!r}，"
            f"发现 {len(connection.tools)} 个工具。"
        )
    except Exception as exc:
        print(f"MCP 连接失败：{exc}")
```

这段代码不判断服务器是不是 GitHub，也不关心它由 Node、Python、Docker 还是其他程序实现。只要该进程通过标准输入输出遵守 MCP，后续流程就完全一致。

在 `chat()` 添加用户消息之前调用一次：

```python
def chat(self, user_text: str) -> str:
    self._ensure_mcp()
    self.messages.append({"role": "user", "content": user_text})
    ...
```

连接后，现有 `_call_model_stream()` 中的 `self.tools.schemas()` 会自动包含 MCP 工具；现有 `_execute_tool()` 也会自动执行代理工具，所以不要增加 `if name.startswith("mcp__")` 的第二套路由。

## 12.6 关闭 MCP 子进程

在 Agent 中增加：

```python
def close(self) -> None:
    if self.mcp is not None:
        self.mcp.close()
        self.mcp = None
```

在 `main()` 运行 one-shot 或 REPL 的部分使用：

```python
try:
    if prompt:
        run_one_shot(agent, prompt, session_id)
    else:
        run_repl(agent, session_id)
finally:
    agent.close()
```

这会替换原来的 `if prompt ... else ...`，不要在后面再执行第二遍。

## 12.7 权限行为

不要把所有 `mcp__` 工具加入 `READ_ONLY_TOOLS`。MCP 工具来自外部服务器，仅凭名称不能确定它是否会修改数据。当前第六章权限策略会把未知工具判为 `confirm`，因此默认模式中的每个 MCP 调用都需要用户确认。

Plan Mode 必须继续保证只读。由于本章无法判断外部 MCP 工具是否会修改数据，在 `check_permission()` 开头的 Plan Mode 判断中再增加：

```python
if agent_mode == "plan" and tool_name.startswith("mcp__"):
    return PermissionResult(
        "deny",
        "Plan Mode 禁止调用行为未知的 MCP 外部工具",
    )
```

因此最终规则是：默认模式下 MCP 工具需要确认，Plan Mode 下 MCP 工具全部拒绝。不能让第十二章绕过第十章已经建立的只读保证。

## 12.8 接入真正的外部 MCP Server

现在不再依赖仓库中的 `mcp-demo-server.mjs`。运行流程统一为：

```text
设置服务器名称、启动命令和参数
→ Agent 启动服务器进程
→ 完成 MCP 握手
→ tools/list 动态发现工具
→ 包装并注册到 ToolRegistry
→ 模型选择工具
→ 权限检查
→ tools/call
```

### 12.8.1 接入 GitHub 官方 MCP Server

先安装并启动 Docker，再创建只具备所需权限的 GitHub PAT。不要把 PAT 写进代码或提交到 Git。

从 `myclaude/myclaude` 的 CMD 运行：

```bat
set "GITHUB_PERSONAL_ACCESS_TOKEN=你的GitHub_PAT"
set "MINI_MCP_NAME=github"
set "MINI_MCP_COMMAND=docker"
set "MINI_MCP_ARGS=["run","-i","--rm","-e","GITHUB_PERSONAL_ACCESS_TOKEN","-e","GITHUB_READ_ONLY=1","-e","GITHUB_TOOLSETS=repos","ghcr.io/github/github-mcp-server"]"
python main.py "使用 GitHub MCP 搜索 topic:backend，按照 stars 降序返回 Star 最多的项目，并给出简介和链接"
```

这里通过 `GITHUB_READ_ONLY=1` 将官方服务器限制为只读，通过 `GITHUB_TOOLSETS=repos` 只加载仓库工具。模型应选择注册后的 `mcp__github__search_repositories`，而不是调用项目内置工具。

### 12.8.2 接入社区 Filesystem MCP Server

Windows 启动 `npx` 时使用 `cmd /c`：

```bat
set "MINI_MCP_NAME=filesystem"
set "MINI_MCP_COMMAND=cmd"
set "MINI_MCP_ARGS=["/c","npx","-y","@modelcontextprotocol/server-filesystem","."]"
python main.py "使用 filesystem MCP 列出当前目录"
```

最后一个 `.` 是允许该服务器访问的目录。这个服务器与现有文件工具能力重叠，因此主要用于验证通用接入能力；项目实际使用时没有必要同时保留两套相同文件工具。

### 12.8.3 接入任意自定义服务器

如果服务器通过 Python 启动，只改变配置：

```bat
set "MINI_MCP_NAME=myserver"
set "MINI_MCP_COMMAND=python"
set "MINI_MCP_ARGS=["path/to/server.py"]"
python main.py "列出并使用 myserver 提供的工具"
```

因此，新增 MCP Server 时不修改 `_ensure_mcp()`，也不新增服务器专用分支。

## 12.9 理解检查

1. MCP 为什么必须先完成 `initialize`，再发送 initialized 通知和 `tools/list`？
2. `mcp__server__tool` 前缀同时解决了哪两个路由问题？
3. 为什么 MCP 工具应该包装成 `Tool` 注册到同一个 Registry？
4. 为什么不能根据 MCP 工具名称直接假定它是只读的？
5. 为什么 Agent 退出时必须关闭 MCP 子进程？
6. MCP Server 为什么不能把普通日志写入承载 JSON-RPC 的标准输出？
7. 为什么连接新 MCP Server 时只修改启动配置，而不应该给 Agent 增加新的 `if`？

## 12.10 本章最终状态

```text
内置 Tool 子类 ─┐
AgentTool ──────┼→ 同一个 ToolRegistry → 同一个权限门 → 同一个 Agent Loop
McpProxyTool ───┘
```

至此，Coding Agent 已经能通过 MCP 动态接入外部工具，同时保留前面章节建立的流式输出、会话、上下文压缩、记忆、Skills、Plan Mode 和子 Agent。
