import json
from mini_claude.model import (
    create_client,
    get_model,
    get_model_capabilities,
)
from mini_claude.tools import create_default_registry, create_tool_context
from mini_claude.prompt import build_system_prompt
from dataclasses import dataclass
import time
from mini_claude.retry import with_retry
from mini_claude.permissions import check_permission
from mini_claude.context import persist_large_result
from mini_claude.context import maybe_compact
from mini_claude.memory import recall_memories
from mini_claude.subagent import run_sub_agent
from mini_claude.mcp_client import (
    McpConnection,
    connect_mcp,
    load_mcp_config,
)
from mini_claude.tools.mcp_tool import McpProxyTool



@dataclass
class StreamResult:
    message: dict
    finish_reason: str


class MINI_CLUE_AGENT:
    def __init__(self, permission_mode: str = "default") -> None:
        self.client = create_client()
        self.model = get_model()
        self.messages: list[dict] = []
        self.tools = create_default_registry()
        self.tool_context = create_tool_context()
        self.tool_context.subagent_runner = lambda task: run_sub_agent(
            task,
            self.client,
            self.model,
            self.tools,
            self.tool_context.project_root,
        )
        self.permission_mode = permission_mode
        self.confirmed_actions: set[str] = set()
        self.mode = "default"
        self.mcp: McpConnection | None = None
        self.mcp_attempted = False


    def _confirm(self, message: str) -> bool:
        try:
            answer = input(f"\n允许执行 {message!r}？[y/N] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            return False
        return answer in {"y", "yes"}

    def set_mode(self, mode: str) -> None:
        if mode not in {"default", "plan"}:
            raise ValueError(f"不支持的 Agent 模式：{mode}")
        self.mode = mode

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


    def chat(self, user_text: str) -> str:
        self._ensure_mcp()
        self.messages.append({"role": "user", "content": user_text})
        while True:
            self.messages = maybe_compact(
                self.messages,
                self.client,
                self.model,
            )
            result = with_retry(lambda: self._call_model_stream(user_text))
            message = result.message
            self.messages.append(message)

            tool_calls = message.get("tool_calls") or []
            if not tool_calls:
                return str(message.get("content") or "")

            for tool_call in tool_calls:
                name = tool_call["function"]["name"]
                try:
                    arguments = json.loads(tool_call["function"]["arguments"])
                except json.JSONDecodeError as exc:
                    tool_result = f"Error: invalid tool arguments: {exc}"
                else:
                    print(f"-> {name}: {arguments}")

                    permission = check_permission(
                        name,
                        arguments,
                        self.permission_mode,
                        self.mode,
                    )

                    if permission.action == "deny":
                        tool_result = f"Action denied: {permission.message}"

                    elif permission.action == "confirm":
                        key = f"{name}:{permission.message}"
                        allowed = (
                                key in self.confirmed_actions
                                or self._confirm(permission.message)
                        )

                        if allowed:
                            self.confirmed_actions.add(key)
                            tool_result = self._execute_tool(name, arguments)
                        else:
                            tool_result = "User denied this action."

                    else:
                        tool_result = self._execute_tool(name, arguments)

                self.messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call["id"],
                        "content": tool_result,
                    }
                )

    def close(self) -> None:
        if self.mcp is not None:
            self.mcp.close()
            self.mcp = None


    def _mode_prompt(self) -> str:
        if self.mode != "plan":
            return ""

        return """

    # Plan Mode Active
    当前处于只读规划模式。
    - 可以读取、搜索和分析项目。
    - 不要调用 write_file、edit_file 或 run_shell。
    - 输出具体实施计划，但不要声称已经完成修改。
    """


    def history(self) -> list[dict]:
        return list(self.messages)

    def load_history(self, messages: list[dict]) -> None:
        self.messages = list(messages)

    def clear_history(self) -> None:
        self.messages = []

    def print_smooth(self, text: str, delay: float = 0.01) -> None:
        for char in text:
            print(char, end="", flush=True)
            time.sleep(delay)



    def _call_model_stream(self, user_context: str) -> StreamResult:

        system_prompt = build_system_prompt()
        system_prompt += self._mode_prompt()
        system_prompt += recall_memories(
            user_context,
            self.tool_context.project_root,
        )

        stream = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system_prompt},
                *self.messages,
            ],
            tools=self.tools.schemas(),
            stream=True,
        )

        content_parts: list[str] = []
        tool_calls: dict[int, dict] = {}
        finish_reason = "stop"
        print("助手：")
        for chunk in stream:
            if not chunk.choices:
                continue

            choice = chunk.choices[0]
            delta = choice.delta

            if delta.content:
                self.print_smooth(delta.content)
                content_parts.append(delta.content)

            if delta.tool_calls:
                for part in delta.tool_calls:
                    current = tool_calls.setdefault(
                        part.index,
                        {"id": "", "name": "", "arguments": ""},
                    )
                    if part.id:
                        current["id"] = part.id
                    if part.function and part.function.name:
                        current["name"] += part.function.name
                    if part.function and part.function.arguments:
                        current["arguments"] += part.function.arguments

            if choice.finish_reason:
                finish_reason = choice.finish_reason

        content = "".join(content_parts)
        assembled_calls = [
            {
                "id": item["id"],
                "type": "function",
                "function": {
                    "name": item["name"],
                    "arguments": item["arguments"],
                },
            }
            for _, item in sorted(tool_calls.items())
        ]

        message: dict = {
            "role": "assistant",
            "content": content or None,
        }
        if assembled_calls:
            message["tool_calls"] = assembled_calls

        if content:
            print()

        return StreamResult(message=message, finish_reason=finish_reason)

    def _execute_tool(self, name: str, arguments: dict) -> str:
        raw_result = self.tools.execute(
            name,
            arguments,
            self.tool_context,
        )
        return persist_large_result(
            name,
            raw_result,
            self.tool_context.project_root,
        )
