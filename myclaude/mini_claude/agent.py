import json
from mini_claude.model import (
    create_client,
    get_model,
    get_model_capabilities,
)
from mini_claude.tools import create_default_registry, create_tool_context
from mini_claude.prompt import build_prompt_parts, build_system_message
from dataclasses import dataclass
import time
from mini_claude.retry import with_retry
from mini_claude.permissions import check_permission
from mini_claude.context import persist_large_result
from mini_claude.context import maybe_compact

from mini_claude.subagent import run_sub_agent
from mini_claude.mcp_client import (
    McpConnection,
    connect_mcp,
    load_mcp_config,
)
from mini_claude.tools.mcp_tool import McpProxyTool

import threading

from mini_claude.scheduler import ToolJob, ToolScheduler
from mini_claude.streaming import StreamResult, collect_stream
from mini_claude.budget import BudgetLimits, BudgetState

from mini_claude.retry import is_prompt_too_long


class MINI_CLUE_AGENT:
    def __init__(
            self,
            permission_mode: str = "default",
            model: str | None = None,
            max_turns: int | None = None,
            max_cost_usd: float | None = None,
            input_price_per_million: float = 0.0,
            output_price_per_million: float = 0.0,
            cache_read_price_per_million: float = 0.0,
    ) -> None:
        self.client = create_client()
        self.model = get_model(model)
        self.model_capabilities = get_model_capabilities()
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
        self.usage: dict = {}
        self.cancelled = threading.Event()
        self.scheduler = ToolScheduler(max_workers=4)
        self.budget = BudgetState(
            BudgetLimits(
                max_turns=max_turns,
                max_cost_usd=max_cost_usd,
                input_price_per_million=input_price_per_million,
                output_price_per_million=output_price_per_million,
                cache_read_price_per_million=(
                    cache_read_price_per_million
                ),
            )
        )


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
        reason = self.budget.stop_reason()
        if reason:
            print(f"Agent 已停止：{reason}")
            return reason
        self.messages.append({"role": "user", "content": user_text})


        while True:
            self.messages = maybe_compact(
                self.messages,
                self.client,
                self.model,
            )
            try:
                result = with_retry(
                    lambda: self._call_model_stream(user_text)
                )
            except Exception as exc:
                if not is_prompt_too_long(exc):
                    raise
                self.messages = maybe_compact(
                    self.messages,
                    self.client,
                    self.model,
                )
                result = self._call_model_stream(user_text)

            self.usage = result.usage
            self.budget.record_usage(result.usage)
            message = result.message
            self.messages.append(message)

            tool_calls = message.get("tool_calls") or []
            if not tool_calls:
                return str(message.get("content") or "")

            tool_messages = self._run_tool_calls(tool_calls)
            self.messages.extend(tool_messages)

            reason = self.budget.stop_reason()
            if reason:
                print(f"Agent 已停止：{reason}")
                return reason

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

        prompt_parts = build_prompt_parts(
            project_root=self.tool_context.project_root,
            mode_prompt=self._mode_prompt(),
            memory_prompt="",
            deferred_names=self.tools.deferred_names(),
        )
        system_message = build_system_message(
            prompt_parts,
            self.model_capabilities,
        )

        stream = self.client.chat.completions.create(
            model=self.model,
            messages=[system_message, *self.messages],
            tools=self.tools.schemas(),
            stream=True,
            stream_options={"include_usage": True},
        )

        print("助手：")
        result = collect_stream(
            stream,
            on_text=self.print_smooth,
        )
        if result.message.get("content"):
            print()
        return result

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

    def _prepare_tool_jobs(
            self,
            tool_calls: list[dict],
    ) -> tuple[list[ToolJob], dict[int, str]]:
        jobs: list[ToolJob] = []
        immediate_results: dict[int, str] = {}

        for index, tool_call in enumerate(tool_calls):
            name = str(tool_call["function"]["name"])
            call_id = str(tool_call["id"])
            try:
                arguments = json.loads(
                    tool_call["function"]["arguments"]
                )
            except json.JSONDecodeError as exc:
                immediate_results[index] = (
                    f"Error: invalid tool arguments: {exc}"
                )
                continue

            print(f"-> {name}: {arguments}")
            permission = check_permission(
                name,
                arguments,
                self.permission_mode,
                self.mode,
            )

            if permission.action == "deny":
                immediate_results[index] = (
                    f"Action denied: {permission.message}"
                )
                continue

            if permission.action == "confirm":
                key = f"{name}:{permission.message}"
                allowed = (
                        key in self.confirmed_actions
                        or self._confirm(permission.message)
                )
                if not allowed:
                    immediate_results[index] = (
                        "User denied this action."
                    )
                    continue
                self.confirmed_actions.add(key)

            tool = self.tools.get(name)
            jobs.append(
                ToolJob(
                    index=index,
                    call_id=call_id,
                    name=name,
                    arguments=arguments,
                    concurrency_safe=bool(
                        tool is not None
                        and tool.concurrency_safe
                    ),
                )
            )

        return jobs, immediate_results

    def _run_tool_calls(self, tool_calls: list[dict]) -> list[dict]:
        jobs, result_by_index = self._prepare_tool_jobs(tool_calls)

        outcomes = self.scheduler.execute(
            jobs,
            execute_one=lambda job: self._execute_tool(
                job.name,
                job.arguments,
            ),
            cancelled=self.cancelled,
        )
        for outcome in outcomes:
            result_by_index[outcome.index] = outcome.content

        tool_messages: list[dict] = []
        for index, tool_call in enumerate(tool_calls):
            tool_messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call["id"],
                    "content": result_by_index.get(
                        index,
                        "Error: tool produced no result",
                    ),
                }
            )
        return tool_messages
