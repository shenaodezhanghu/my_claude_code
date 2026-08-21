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
from pathlib import Path

from mini_claude.session_workspace import create_session_workspace
from mini_claude.plan import PlanState

from mini_claude.workspace import WorkspacePolicy

from mini_claude.permissions import check_path_access

from mini_claude.session import load_runtime_state, save_runtime_state
from mini_claude.cancellation import AgentCancelled

from mini_claude.context import summary_compact, persist_large_result, maybe_compact
from mini_claude.prompt_cache import PromptBuildCache

class MINI_CLUE_AGENT:
    def __init__(
            self,
            session_id: str,
            permission_mode: str = "default",
            model: str | None = None,
            project_root: Path | None = None,
            workspace_policy: WorkspacePolicy | None = None,
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
        root = (project_root or Path.cwd()).resolve()
        self.session_workspace = create_session_workspace(
            root,
            session_id,
        )
        self.workspace_policy = (
                workspace_policy or WorkspacePolicy(root)
        )
        self.cancelled = threading.Event()
        self.tool_context = create_tool_context(
            self.workspace_policy.workspace_root,
            self.session_workspace,
            self.workspace_policy,
            self.cancelled,
        )
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
        self.prompt_cache = PromptBuildCache()

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
        self.plan = PlanState(
            session_id=session_id,
            project_root=self.tool_context.project_root,
        )
        self.tool_context.enter_plan_runner = self._enter_plan_mode
        self.tool_context.exit_plan_runner = self._exit_plan_mode
        self._running = False
        self._active_stream = None
        self.last_timings: dict[str, float] = {}
        self.pending_verification = False


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

    @property
    def is_running(self) -> bool:
        return self._running

    def request_cancel(self) -> bool:
        if not self._running:
            return False
        self.cancelled.set()
        stream = self._active_stream
        if stream is not None and hasattr(stream, "close"):
            stream.close()
        return True

    def _enter_plan_mode(self) -> str:
        message = self.plan.enter()
        self.mode = "plan"
        return message

    def _exit_plan_mode(self) -> str:
        return self.plan.exit_for_review()

    def enter_plan_mode(self) -> str:
        return self._enter_plan_mode()

    def exit_plan_mode(self) -> str:
        return self._exit_plan_mode()

    def leave_plan_mode(self) -> str:
        self.mode = "default"
        self.plan.active = False
        self.plan.awaiting_review = False
        return "已退出 Plan Mode，Plan 文件仍然保留。"

    def set_model(self, model: str) -> None:
        self.model = get_model(model)

    def compact_now(self) -> bool:
        before = self.messages

        compacted = summary_compact(
            before,
            self.client,
            self.model,
            force=True,
        )

        if compacted == before:
            return False

        self.messages = compacted
        return True

    def chat(self, user_text: str) -> str:
        self.cancelled.clear()
        self._running = True
        try:
            return self._chat_loop(user_text)
        except AgentCancelled:
            return "当前任务已取消。"
        finally:
            self._running = False
            self._save_runtime_state()

    def _chat_loop(self, user_text: str) -> str:
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

            raw_usage = result.usage
            self.budget.record_usage(raw_usage)

            if raw_usage is None:
                self.usage = {}
            elif isinstance(raw_usage, dict):
                self.usage = raw_usage
            elif hasattr(raw_usage, "model_dump"):
                self.usage = raw_usage.model_dump(exclude_none=True)
            else:
                self.usage = {}
            message = result.message
            self.messages.append(message)

            tool_calls = message.get("tool_calls") or []
            if not tool_calls:
                if self.mode == "plan" and self.plan.active:
                    plan_content = self.plan.read().strip()

                    if plan_content == "# Implementation Plan":
                        self.messages.append(
                            {
                                "role": "user",
                                "content": (
                                    "当前处于 Plan Mode，但 Plan 文件仍为空。"
                                    "请使用 write_file 或 edit_file 完成 Plan 文件，"
                                    "然后调用 exit_plan_mode。"
                                ),
                            }
                        )
                        continue

                    self.plan.exit_for_review()

                if self.plan.awaiting_review:
                    if self._review_plan():
                        continue

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
        return f"""Plan Mode 已启用。
    你必须先读取和分析代码，再把实施计划写入：
    {self.plan.relative_path}

    这是唯一允许修改的文件。
    禁止修改其他文件，禁止运行 Shell，禁止调用行为未知的 MCP 工具。
    计划必须包含：背景、实施步骤、关键文件和验证方法。
    计划完成后调用 exit_plan_mode，不要直接询问用户是否批准。"""


    def history(self) -> list[dict]:
        return list(self.messages)

    def load_history(self, messages: list[dict]) -> None:
        self.messages = list(messages)

    def clear_history(self) -> None:
        self.messages = []

    def print_smooth(
            self,
            text: str,
            delay: float = 0.01,
    ) -> None:
        for char in text:
            if self.cancelled.is_set():
                raise AgentCancelled("输出已取消")

            print(char, end="", flush=True)
            time.sleep(delay)

    def _save_runtime_state(self) -> None:
        limits = self.budget.limits
        state = {
            "version": 2,
            "session_id": self.session_workspace.session_id,
            "model": self.model,
            "permission_mode": self.permission_mode,
            "mode": self.mode,
            "activated_tools": self.tools.activated_names(),
            "workspace": self.workspace_policy.to_dict(),
            "budget_limits": {
                "max_turns": limits.max_turns,
                "max_cost_usd": limits.max_cost_usd,
                "input_price_per_million": (
                    limits.input_price_per_million
                ),
                "output_price_per_million": (
                    limits.output_price_per_million
                ),
                "cache_read_price_per_million": (
                    limits.cache_read_price_per_million
                ),
            },
            "budget_usage": self.budget.to_dict(),
            "plan": self.plan.to_dict(),
            "last_usage": self.usage,
        }
        save_runtime_state(
            self.session_workspace.state_file,
            state,
        )

    def restore_runtime_state(self, state: dict) -> None:
        if not state:
            return
        version = int(state.get("version", 1))
        if version not in {1, 2}:
            raise RuntimeError(
                f"不支持的 Session 状态版本：{version}"
            )

        self.mode = str(state.get("mode", "default"))
        if self.mode not in {"default", "plan"}:
            self.mode = "default"

        self.tools.restore_activated(
            list(state.get("activated_tools", []))
        )
        self.budget.restore(
            dict(state.get("budget_usage", {}))
        )
        self.plan.restore(dict(state.get("plan", {})))
        self.usage = dict(state.get("last_usage", {}))


    def _call_model_stream(self, user_context: str) -> StreamResult:

        prepare_started = time.perf_counter()
        prompt_started = time.perf_counter()
        prompt_parts = build_prompt_parts(
            project_root=self.tool_context.project_root,
            mode_prompt=self._mode_prompt(),
            memory_prompt="",
            deferred_names=self.tools.deferred_names(),
            cache=self.prompt_cache,
        )
        system_message = build_system_message(
            prompt_parts,
            self.model_capabilities,
        )
        prompt_build_ms = (time.perf_counter() - prompt_started) * 1000

        schema_started = time.perf_counter()
        tool_schemas = self.tools.schemas()
        schema_build_ms = (time.perf_counter() - schema_started) * 1000
        prepare_total_ms = (time.perf_counter() - prepare_started) * 1000
        self.last_timings = {
            "prompt_build_ms": prompt_build_ms,
            "schema_build_ms": schema_build_ms,
            "prepare_total_ms": prepare_total_ms,
        }

        stream = self.client.chat.completions.create(
            model=self.model,
            messages=[system_message, *self.messages],
            tools=tool_schemas,
            stream=True,
            stream_options={"include_usage": True},
        )

        self._active_stream = stream
        print("助手：")
        try:
            result = collect_stream(
                stream,
                on_text=self.print_smooth,
                cancelled=self.cancelled,
            )
        except Exception as exc:
            if self.cancelled.is_set():
                raise AgentCancelled("模型调用已取消") from exc
            raise
        finally:
            self._active_stream = None
            if hasattr(stream, "close"):
                stream.close()
        if result.message.get("content"):
            print()
        return result

    def _execute_tool(self, name: str, arguments: dict) -> str:
        raw_result = self.tools.execute(
            name,
            arguments,
            self.tool_context,
        )
        if name in {"write_file", "edit_file", "run_shell"}:
            self.prompt_cache.mark_git_dirty()
        return persist_large_result(
            name,
            raw_result,
            self.tool_context.project_root,
            self.session_workspace.tool_results_dir,
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
            plan_file = self.plan.relative_path
            permission = check_permission(
                name,
                arguments,
                self.permission_mode,
                self.mode,
                plan_file,
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

            policy = self.tool_context.workspace_policy
            if policy is not None:
                path_access = check_path_access(
                    name,
                    arguments,
                    policy,
                )
                if path_access.action == "confirm":
                    allowed = self._confirm(path_access.message)
                    if not allowed or path_access.grant_root is None:
                        immediate_results[index] = (
                            "User denied external path access."
                        )
                        continue
                    try:
                        policy.grant(
                            path_access.grant_root,
                            path_access.access,
                        )
                    except PermissionError as exc:
                        immediate_results[index] = f"Action denied: {exc}"
                        continue
                    self._save_runtime_state()

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

    def _review_plan(self) -> bool:
        print("\n当前 Plan：\n")
        print(self.plan.read())
        print(
            "\n1. 清空规划历史并执行"
            "\n2. 保留历史并执行"
            "\n3. 手动处理，不执行"
            "\n4. 提供建议，继续修改计划"
            "\n5. 接受计划，返回对话但不执行"
        )
        try:
            choice = input("请选择 [1-5]：").strip()
        except (EOFError, KeyboardInterrupt):
            choice = "3"

        if choice == "1":
            self.messages = []
            self.mode = "default"
            self.permission_mode = "accept_edits"
            self.plan.awaiting_review = False
            self.messages.append(
                {
                    "role": "user",
                    "content": (
                            "执行已批准的 Plan：\n"
                            + self.plan.read()
                    ),
                }
            )
            return True

        if choice == "2":
            self.mode = "default"
            self.permission_mode = "accept_edits"
            self.plan.awaiting_review = False
            self.messages.append(
                {
                    "role": "user",
                    "content": (
                            "用户已批准 Plan。现在按照 Plan 执行。\n"
                            + self.plan.read()
                    ),
                }
            )
            return True

        if choice == "4":
            try:
                feedback = input("请输入修改意见：").strip()
            except (EOFError, KeyboardInterrupt):
                feedback = "继续检查并完善计划"
            self.mode = "plan"
            self.plan.active = True
            self.plan.awaiting_review = False
            self.messages.append(
                {
                    "role": "user",
                    "content": f"请根据反馈继续规划：{feedback}",
                }
            )
            return True

        if choice == "5":
            self.mode = "default"
            self.plan.active = False
            self.plan.awaiting_review = False
            return False

        self.mode = "default"
        self.plan.awaiting_review = False
        return False

