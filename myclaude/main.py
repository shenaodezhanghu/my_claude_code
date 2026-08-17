import argparse
from dotenv import load_dotenv
from mini_claude.agent import MINI_CLUE_AGENT
from uuid import uuid4
from pathlib import Path
from mini_claude.skills import resolve_skill
from mini_claude.session_index import (
    find_session_root,
    register_session,
    list_session_entries,
)
from mini_claude.workspace import WorkspacePolicy
from mini_claude.session import (
    SessionStateError,
    load_runtime_state,
    migrate_runtime_state,
    load_session,
    save_session,
)
from mini_claude.session_workspace import (
    create_session_workspace,
)
import signal
from collections.abc import Callable

from mini_claude.commands import CommandRegistry, CommandSpec


ENV_FILE = Path(__file__).resolve().parent / ".env"
load_dotenv(ENV_FILE)
INPUT_PRICE = 2
OUTPUT_PRICE = 8
CACHE_READ_PRICE = 0.4


class CliApplication:
    def __init__(
        self,
        agent: MINI_CLUE_AGENT,
        session_id: str,
        open_session: Callable[
            [str | None, Path | None],
            tuple[MINI_CLUE_AGENT, str],
        ],
        list_sessions: Callable[[], list[dict]],
    ) -> None:
        self.agent = agent
        self.session_id = session_id
        self.open_session = open_session
        self.list_sessions = list_sessions
        self.running = True
        self._agent_closed = False
        self.commands = CommandRegistry()
        self._register_commands()

    def _register_commands(self) -> None:
        specs = [
            ("/help", "/help", "显示所有命令", self._help),
            ("/status", "/status", "显示会话、Mode、模型和预算", self._status),
            ("/history", "/history", "显示对话角色和内容摘要", self._history),
            ("/sessions", "/sessions", "列出可恢复会话", self._sessions),
            ("/resume", "/resume <id>", "切换到已有会话", self._resume),
            ("/new", "/new [目录]", "创建并切换到新会话", self._new),
            ("/mode", "/mode <default|plan>", "切换运行模式", self._mode),
            ("/model", "/model [名称]", "查看或切换模型", self._model),
            ("/cwd", "/cwd", "显示当前会话工作区", self._cwd),
            ("/permissions", "/permissions", "显示目录和权限状态", self._permissions),
            ("/compact", "/compact", "立即压缩旧上下文", self._compact),
            ("/clear", "/clear", "清空当前会话消息", self._clear),
            ("/exit", "/exit", "保存并退出", self._exit),
        ]
        for name, usage, description, handler in specs:
            self.commands.register(
                CommandSpec(name, usage, description, handler)
            )

    def _help(self, args: list[str]) -> str:
        return self.commands.help_text()

    def _status(self, args: list[str]) -> str:
        reason = self.agent.budget.stop_reason() or "未超限"
        return (
            f"Session: {self.session_id}\n"
            f"Workspace: {self.agent.workspace_policy.workspace_root}\n"
            f"Mode: {self.agent.mode}\n"
            f"Model: {self.agent.model}\n"
            f"Permission: {self.agent.permission_mode}\n"
            f"Budget: {reason}"
        )

    def history_text(self) -> str:
        rows: list[str] = []
        for message in self.agent.history():
            role = message.get("role", "unknown")
            content = str(message.get("content") or "")
            rows.append(f"{role}: {content[:100]}")
        return "\n".join(rows) or "当前会话没有消息。"

    def _history(self, args: list[str]) -> str:
        return self.history_text()

    def _sessions(self, args: list[str]) -> str:
        rows = []
        for item in self.list_sessions():
            marker = "*" if item["session_id"] == self.session_id else " "
            rows.append(
                f"{marker} {item['session_id']}  {item['workspace_root']}"
            )
        return "\n".join(rows) or "没有可恢复会话。"

    def _cwd(self, args: list[str]) -> str:
        return str(self.agent.workspace_policy.workspace_root)

    def _permissions(self, args: list[str]) -> str:
        policy = self.agent.workspace_policy
        read_roots = "\n".join(
            f"  - {path}"
            for path in sorted(policy.read_roots, key=str)
        )
        write_roots = "\n".join(
            f"  - {path}"
            for path in sorted(policy.write_roots, key=str)
        )
        return (
            f"Permission mode: {self.agent.permission_mode}\n"
            f"Read roots:\n{read_roots}\n"
            f"Write roots:\n{write_roots}"
        )

    def _mode(self, args: list[str]) -> str:
        if len(args) != 1 or args[0] not in {"default", "plan"}:
            return "用法：/mode <default|plan>"
        if args[0] == "plan":
            result = self.agent.enter_plan_mode()
        else:
            result = self.agent.leave_plan_mode()
        self.agent._save_runtime_state()
        return result

    def _model(self, args: list[str]) -> str:
        if not args:
            return f"当前模型：{self.agent.model}"
        if len(args) != 1:
            return "用法：/model [名称]"
        self.agent.set_model(args[0])
        self.agent._save_runtime_state()
        return f"已切换模型：{self.agent.model}"

    def _compact(self, args: list[str]) -> str:
        before = len(self.agent.history())
        changed = self.agent.compact_now()
        after = len(self.agent.history())
        self.agent._save_runtime_state()
        return (
            f"上下文已压缩：{before} → {after} 条消息"
            if changed
            else "当前上下文不需要压缩。"
        )

    def _clear(self, args: list[str]) -> str:
        self.agent.clear_history()
        save_session(
            self.agent.session_workspace,
            self.agent.history(),
        )
        return "当前会话消息已清空。"



    def _close_current(self) -> None:
        if self._agent_closed:
            return
        save_session(
            self.agent.session_workspace,
            self.agent.history(),
        )
        self.agent._save_runtime_state()
        self.agent.close()
        self._agent_closed = True

    def _resume(self, args: list[str]) -> str:
        if len(args) != 1:
            return "用法：/resume <session_id>"
        if args[0] == self.session_id:
            return "当前已经是该会话。"

        new_agent, new_id = self.open_session(args[0], None)
        self._close_current()
        self.agent = new_agent
        self.session_id = new_id
        self._agent_closed = False
        return (
            f"已恢复会话 {new_id}，"
            f"共 {len(new_agent.history())} 条消息。\n"
            f"{self.history_text()}"
        )

    def _new(self, args: list[str]) -> str:
        if len(args) > 1:
            return "用法：/new [目录]"
        cwd = (
            Path(args[0]).resolve()
            if args
            else self.agent.workspace_policy.workspace_root
        )
        new_agent, new_id = self.open_session(None, cwd)
        self._close_current()
        self.agent = new_agent
        self.session_id = new_id
        self._agent_closed = False
        return f"已创建会话 {new_id}，工作区：{cwd}"

    def _exit(self, args: list[str]) -> str:
        self._close_current()
        self.running = False
        return "会话已保存。"




def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="一个从零实现的 Coding Agent")
    parser.add_argument("prompt", nargs="*", help="要交给 Agent 的任务")
    parser.add_argument("--model", "-m", help="覆盖当前会话使用的模型", default=None)
    parser.add_argument("--permission-mode", choices=["default", "accept_edits", "dont_ask"], default=None, )
    parser.add_argument("--plan", action="store_true", help="以只读规划模式运行", )
    parser.add_argument("--cwd", type=Path, help="新会话的目标项目根目录", )
    session_group = parser.add_mutually_exclusive_group()

    session_group.add_argument(
        "--new",
        action="store_true",
        help="明确创建新会话",
    )

    session_group.add_argument(
        "--resume",
        metavar="SESSION_ID",
        help="恢复指定会话",
    )
    return parser.parse_args()


def install_interrupt_handler(
    get_agent: Callable[[], MINI_CLUE_AGENT],
) -> None:
    def handle_interrupt(signum, frame) -> None:
        agent = get_agent()
        if agent.is_running and not agent.cancelled.is_set():
            print("\n正在取消当前任务，再按一次 Ctrl+C 强制退出。")
            agent.request_cancel()
            return
        raise KeyboardInterrupt

    signal.signal(signal.SIGINT, handle_interrupt)


def show_cache(agent: MINI_CLUE_AGENT) -> None:
    usage = agent.usage or {}
    details = usage.get("prompt_tokens_details") or {}

    input_tokens = int(usage.get("prompt_tokens", 0))
    output_tokens = int(
        usage.get("completion_tokens", 0)
    )
    total_tokens = int(usage.get("total_tokens", 0))
    cached_tokens = int(details.get("cached_tokens", 0))

    normal_input_tokens = max(
        0,
        input_tokens - cached_tokens,
    )

    limits = agent.budget.limits

    current_cost = (
        normal_input_tokens
        * limits.input_price_per_million
        / 1_000_000
    )
    current_cost += (
        cached_tokens
        * limits.cache_read_price_per_million
        / 1_000_000
    )
    current_cost += (
        output_tokens
        * limits.output_price_per_million
        / 1_000_000
    )

    print(
        "\nToken 统计："
        f"\n  输入：{input_tokens}"
        f"\n  普通输入：{normal_input_tokens}"
        f"\n  缓存命中：{cached_tokens}"
        f"\n  输出：{output_tokens}"
        f"\n  总计：{total_tokens}"
        f"\n  本轮费用：¥{current_cost:.6f}"
        f"\n  累计费用："
        f"¥{agent.budget.estimated_cost_usd:.6f}"
    )

def resolve_user_input(agent: MINI_CLUE_AGENT, text: str) -> str:
    return resolve_skill(
        text,
        agent.tool_context.project_root,
    ) or text


def run_one_shot(agent: MINI_CLUE_AGENT, prompt: str, session_id: str) -> None:
    agent.chat(resolve_user_input(agent, prompt))
    show_cache(agent)
    save_session(agent.session_workspace, agent.history())


def show_history(agent: MINI_CLUE_AGENT) -> None:
    for message in agent.history():
        role = message.get("role", "unknown")
        content = str(message.get("content") or "")
        print(f"{role}: {content}")




def run_repl(app: CliApplication) -> None:
    print("mini-agent：输入任务，/help 查看命令。")
    while app.running:
        try:
            line = input("你：").strip()
        except KeyboardInterrupt:
            print("\n当前没有运行任务，输入 /exit 退出。")
            continue

        if not line:
            continue

        handled, output = app.commands.dispatch(line)
        if handled:
            if output:
                print(output)
            continue

        expanded = resolve_skill(
            line,
            app.agent.tool_context.project_root,
        )
        if line.startswith("/") and expanded is None:
            print(
                f"未知命令或 Skill：{line.split()[0]}，"
                "输入 /help 查看内置命令。"
            )
            continue
        user_text = expanded or line
        try:
            app.agent.chat(user_text)
        finally:
            save_session(
                app.agent.session_workspace,
                app.agent.history(),
            )
            app.agent._save_runtime_state()
        show_cache(app.agent)


def open_session(
    session_id: str | None,
    cwd: Path | None,
    args: argparse.Namespace,
    *,
    enter_plan: bool = False,
) -> tuple[MINI_CLUE_AGENT, str]:
    restoring = session_id is not None

    # 1. 确定会话 ID 和工作区
    if restoring:
        workspace_root = find_session_root(session_id)

        if workspace_root is None:
            raise RuntimeError(
                f"找不到会话 {session_id!r} 对应的工作区"
            )

        if cwd is not None:
            requested_root = cwd.resolve()
            if requested_root != workspace_root:
                raise RuntimeError(
                    "--resume 指定的会话与 --cwd 工作区不一致"
                )
    else:
        session_id = uuid4().hex
        workspace_root = (
            cwd.resolve()
            if cwd is not None
            else Path.cwd().resolve()
        )

        if not workspace_root.is_dir():
            raise RuntimeError(
                f"工作区目录不存在：{workspace_root}"
            )

    # 2. 定位当前会话文件夹
    workspace = create_session_workspace(
        workspace_root,
        session_id,
    )

    # 3. 读取并迁移 state.json
    try:
        runtime_state = migrate_runtime_state(
            load_runtime_state(workspace.state_file)
        )
    except SessionStateError as exc:
        raise RuntimeError(
            f"会话 {session_id} 无法安全恢复：{exc}"
        ) from exc

    # 4. 验证 state.json 中的 Session ID
    saved_session_id = runtime_state.get("session_id")
    if (
        saved_session_id is not None
        and saved_session_id != session_id
    ):
        raise RuntimeError(
            "state.json 中的 session_id 与当前会话不一致"
        )

    # 5. 恢复工作区和外部目录授权
    workspace_value = runtime_state.get("workspace")

    if (
        isinstance(workspace_value, dict)
        and workspace_value.get("workspace_root")
    ):
        workspace_policy = WorkspacePolicy.from_dict(
            workspace_value
        )

        if workspace_policy.workspace_root != workspace_root:
            raise RuntimeError(
                "Session Index 与 state.json 中的工作区不一致"
            )
    else:
        workspace_policy = WorkspacePolicy(workspace_root)

    # 6. 解析模型和权限
    resolved_model = (
        args.model
        or runtime_state.get("model")
    )

    resolved_permission_mode = (
        args.permission_mode
        or runtime_state.get("permission_mode")
        or "default"
    )

    # 7. 恢复价格；没有保存值时使用 main.py 顶部常量
    saved_limits = runtime_state.get("budget_limits") or {}

    saved_input_price = saved_limits.get(
        "input_price_per_million"
    )
    resolved_input_price = (
        INPUT_PRICE
        if saved_input_price is None
        else float(saved_input_price)
    )

    saved_output_price = saved_limits.get(
        "output_price_per_million"
    )
    resolved_output_price = (
        OUTPUT_PRICE
        if saved_output_price is None
        else float(saved_output_price)
    )

    saved_cache_read_price = saved_limits.get(
        "cache_read_price_per_million"
    )
    resolved_cache_read_price = (
        CACHE_READ_PRICE
        if saved_cache_read_price is None
        else float(saved_cache_read_price)
    )

    # 8. 创建 Agent
    agent = MINI_CLUE_AGENT(
        session_id=session_id,
        permission_mode=resolved_permission_mode,
        model=resolved_model,
        project_root=workspace_root,
        workspace_policy=workspace_policy,
        input_price_per_million=resolved_input_price,
        output_price_per_million=resolved_output_price,
        cache_read_price_per_million=(
            resolved_cache_read_price
        ),
    )

    # 9. 恢复消息和运行状态
    if restoring:
        history = load_session(agent.session_workspace)
        agent.load_history(history)
        agent.restore_runtime_state(runtime_state)

    # 10. 本次显式 --plan 最后生效
    if enter_plan:
        print(agent._enter_plan_mode())

    # 11. 保存合并后的最新状态
    agent._save_runtime_state()

    # Agent 成功创建后再写全局索引
    if not restoring:
        register_session(session_id, workspace_root)

    return agent, session_id


def main() -> None:
    args = parse_args()

    # 创建或恢复程序启动时的第一个会话
    initial_agent, initial_id = open_session(
        args.resume,
        args.cwd,
        args,
        enter_plan=args.plan,
    )

    # CLI 应用负责持有当前 Agent
    app = CliApplication(
        initial_agent,
        initial_id,

        # /new 和 /resume 会调用这个函数切换会话
        open_session=lambda session_id, cwd: open_session(
            session_id,
            cwd,
            args,
            enter_plan=False,
        ),

        # /sessions 使用这个函数显示全部会话
        list_sessions=list_session_entries,
    )
    install_interrupt_handler(lambda: app.agent)

    # 命令行使用 --resume 时直接显示恢复的历史
    if args.resume:
        print(
            f"已恢复 {len(app.agent.history())} 条消息。\n"
            f"{app.history_text()}"
        )

    prompt = " ".join(args.prompt).strip()

    try:
        if prompt:
            run_one_shot(
                app.agent,
                prompt,
                app.session_id,
            )
        else:
            run_repl(app)
    finally:
        app._close_current()



if __name__ == "__main__":
    main()
