# 第十六章 从 Mini-Claude 内核到本地 Agent 运行时

> 本章采用 KamaClaude 的工程路线：不再继续给单进程脚本堆功能，而是把已经完成的 Mini-Claude 内核迁移为“Core daemon + CLI/TUI 客户端 + 事件流 + 分层状态”的本地 Agent 运行时。
>
> 参考：[KamaClaude：从零实现一个本地 Claude Code Agent 系统](https://www.programmercarl.com/other/project_kamaClaude.html)。公开页面说明了架构与能力边界，没有公开全部实现代码，因此本章按照其架构思想，为当前 Python 项目设计可运行的教学实现，而不是声称逐行复刻 KamaClaude 源码。

## 16.1 前置条件

开始本章前，必须完成第十三章，而不是从当前 13.6.2 直接跳到这里。

至少确认以下能力已经可以独立运行：

- static/dynamic Prompt 与模型能力已经接入。
- Deferred Tools、`tool_search` 和工具调度器已经完成。
- Budget、取消、四层上下文和 `RunStats` 已经完成。
- 完整 Plan Mode、Sub-Agent Registry 和多 MCP Server 已经完成。
- 第十四章测试全部通过。

本章不重新实现这些能力，而是改变它们的运行边界。

## 16.2 为什么现有架构需要继续演进

### 16.2.1 当前问题

当前 `main.py` 直接创建 Agent，Agent 内部又直接：

- 调用模型；
- 执行工具；
- 使用 `print()` 展示过程；
- 使用 `input()` 请求权限；
- 保存消息历史。

这在学习 Agent Loop 时很清楚，但会带来五个工程问题：

1. CLI 一旦退出，正在执行的任务也会退出。
2. TUI、CLI 和未来 Web 页面无法复用同一个任务。
3. 工具调用和权限审批只能显示，无法订阅或回放。
4. Session 只有消息列表，无法表达 Thread、Run、Notes 和子 Agent。
5. 测试必须拦截终端输入输出，很难单独测试运行逻辑。

### 16.2.2 解决方案

按照 KamaClaude 路线，把系统拆为：

```text
用户
  → CLI / TUI
  → JSON-RPC 2.0 over NDJSON
  → mini-core daemon
  → AgentRunner
  → 现有 Agent Loop
  → ToolRegistry / PermissionManager / ContextManager
  → EventBus
  → SessionStore / events.jsonl / trace.jsonl
```

关键原则是：

- Core 执行任务，客户端只负责输入和展示。
- Agent 不把执行过程直接打印出来，而是发布事件。
- 权限确认不是 Agent 调用 `input()`，而是一次可等待、可恢复的审批事件。
- 所有重要过程都先成为结构化事件，再决定显示或保存在哪里。

### 16.2.3 本章迁移顺序

```text
事件模型
→ EventBus
→ AgentRunner
→ Session / Thread / Run / Notes
→ events 与 trace
→ 权限审批代理
→ JSON-RPC + NDJSON
→ Core daemon
→ CLI 客户端
→ TUI
→ Task 规划与 Research Mode
→ 工程质量验收
```

虽然 KamaClaude 从 S0 就划分 CLI 和 daemon，但当前项目已经存在 Agent Loop。这里先建立事件和 Runner，是为了在迁移时保持每一步都能运行，随后立即建立 daemon 边界。

## 16.3 第一阶段：建立结构化事件模型

### 16.3.1 要解决的问题

当前 `agent.py` 中的文本、工具调用和压缩提示都是普通 `print()`。终端看得到，人和程序却无法可靠判断“这是 Token、工具开始、权限申请还是任务完成”。

### 16.3.2 解决方案

建立统一 `RuntimeEvent`：

- `type` 表示事件类型；
- `session_id`、`thread_id` 和 `run_id` 表示归属；
- `data` 保存事件负载；
- `sequence` 保证同一 Core 内的顺序；
- `timestamp` 用于回放。

### 16.3.3 新建 `mini_claude/runtime/__init__.py`

先创建 `mini_claude/runtime/` 目录，再新建空的 `__init__.py`。

### 16.3.4 新建完整 `mini_claude/runtime/events.py`

```python
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4


@dataclass(frozen=True)
class RuntimeEvent:
    type: str
    session_id: str
    thread_id: str
    run_id: str
    data: dict[str, Any] = field(default_factory=dict)
    sequence: int = 0
    event_id: str = field(default_factory=lambda: uuid4().hex)
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
```

事件类型统一使用点分名称：

```text
run.started          run.completed          run.failed
model.token          model.completed
tool.requested       tool.started           tool.completed
permission.requested permission.resolved
context.warning      context.compacted
task.created         task.updated
subagent.started     subagent.completed
```

不要让不同模块自己发明近义名称。

### 16.3.5 新建完整 `mini_claude/runtime/event_bus.py`

```python
from __future__ import annotations

from collections.abc import Callable
from threading import RLock

from mini_claude.runtime.events import RuntimeEvent


EventHandler = Callable[[RuntimeEvent], None]


class EventBus:
    def __init__(self) -> None:
        self._handlers: list[EventHandler] = []
        self._sequence = 0
        self._lock = RLock()

    def subscribe(self, handler: EventHandler) -> Callable[[], None]:
        with self._lock:
            self._handlers.append(handler)

        def unsubscribe() -> None:
            with self._lock:
                if handler in self._handlers:
                    self._handlers.remove(handler)

        return unsubscribe

    def publish(self, event: RuntimeEvent) -> RuntimeEvent:
        with self._lock:
            self._sequence += 1
            published = RuntimeEvent(
                type=event.type,
                session_id=event.session_id,
                thread_id=event.thread_id,
                run_id=event.run_id,
                data=event.data,
                sequence=self._sequence,
                event_id=event.event_id,
                timestamp=event.timestamp,
            )
            handlers = tuple(self._handlers)

        for handler in handlers:
            try:
                handler(published)
            except Exception:
                # 一个展示器失败，不能中断 Agent 任务。
                continue
        return published
```

这里使用锁不是为了让 Agent Loop 并行，而是因为后面模型执行线程、IPC 线程和持久化订阅器可能同时发布或接收事件。

### 16.3.6 增加测试 `tests/test_event_bus.py`

```python
from mini_claude.runtime.event_bus import EventBus
from mini_claude.runtime.events import RuntimeEvent


def test_event_bus_assigns_monotonic_sequence() -> None:
    bus = EventBus()
    received: list[RuntimeEvent] = []
    bus.subscribe(received.append)

    for event_type in ("run.started", "run.completed"):
        bus.publish(
            RuntimeEvent(event_type, "s1", "t1", "r1")
        )

    assert [event.sequence for event in received] == [1, 2]
```

运行：

```bat
python -m pytest tests/test_event_bus.py -q
```

## 16.4 第二阶段：把 Agent 输出迁移到事件

### 16.4.1 要解决的问题

如果只增加 EventBus，但 `agent.py` 仍然直接 `print()`，系统就会同时存在两条输出路径，CLI 可能把同一段文字显示两遍。

### 16.4.2 解决方案

给 Agent 注入一个事件回调。Agent 只发送事件；兼容 CLI 通过订阅事件完成原来的打印。

### 16.4.3 给 Agent 增加运行绑定

在 `agent.py` 的导入区增加：

```python
from collections.abc import Callable
from typing import Any

from mini_claude.runtime.events import RuntimeEvent
```

在构造函数参数中增加：

```python
event_sink: Callable[[RuntimeEvent], None] | None = None,
```

在构造函数末尾增加：

```python
self.event_sink = event_sink
self.session_id = ""
self.thread_id = ""
self.run_id = ""
```

在类中新增：

```python
def bind_run(
    self,
    session_id: str,
    thread_id: str,
    run_id: str,
) -> None:
    self.session_id = session_id
    self.thread_id = thread_id
    self.run_id = run_id

def _emit(self, event_type: str, **data: Any) -> None:
    if self.event_sink is None:
        return
    self.event_sink(
        RuntimeEvent(
            type=event_type,
            session_id=self.session_id,
            thread_id=self.thread_id,
            run_id=self.run_id,
            data=data,
        )
    )
```

### 16.4.4 逐项替换直接输出

收到流式文本时，将：

```python
self.print_smooth(delta.content)
```

替换为：

```python
self._emit("model.token", text=delta.content)
```

工具参数组装完成后、执行前发布：

```python
self._emit(
    "tool.requested",
    tool_call_id=tool_call["id"],
    name=name,
    arguments=arguments,
)
```

工具执行前后分别发布：

```python
self._emit("tool.started", name=name, arguments=arguments)
```

```python
self._emit(
    "tool.completed",
    name=name,
    result=tool_result,
)
```

上下文压缩处发布 `context.compacted`。完成这些替换后，删除 Agent Loop 中只用于界面的 `print()`，但保留 `print_smooth()` 到本阶段结束；确认 CLI 已迁移后再删除它。

## 16.5 第三阶段：建立 AgentRunner

### 16.5.1 要解决的问题

Agent 表示“如何思考和调用工具”，但一次真实任务还需要 Run ID、状态、开始结束时间、取消和异常处理。这些不应继续放进 Agent 类。

### 16.5.2 解决方案

新增 `AgentRunner`，一个 Runner 负责一次 Run 的生命周期，Agent 只负责循环。

### 16.5.3 新建完整 `mini_claude/runtime/runner.py`

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
from uuid import uuid4

from mini_claude.runtime.event_bus import EventBus
from mini_claude.runtime.events import RuntimeEvent


class RunnableAgent(Protocol):
    def bind_run(
        self,
        session_id: str,
        thread_id: str,
        run_id: str,
    ) -> None: ...

    def chat(self, goal: str) -> str: ...

    def history(self) -> list[dict]: ...


@dataclass(frozen=True)
class RunResult:
    run_id: str
    status: str
    answer: str = ""
    error: str = ""


class AgentRunner:
    def __init__(self, agent: RunnableAgent, bus: EventBus) -> None:
        self.agent = agent
        self.bus = bus

    def run(
        self,
        goal: str,
        session_id: str,
        thread_id: str,
        run_id: str | None = None,
    ) -> RunResult:
        resolved_run_id = run_id or uuid4().hex
        self.agent.bind_run(
            session_id,
            thread_id,
            resolved_run_id,
        )
        self._publish(
            "run.started",
            session_id,
            thread_id,
            resolved_run_id,
            {"goal": goal},
        )
        try:
            answer = self.agent.chat(goal)
        except Exception as exc:
            self._publish(
                "run.failed",
                session_id,
                thread_id,
                resolved_run_id,
                {"error": str(exc)},
            )
            return RunResult(
                resolved_run_id,
                "failed",
                error=str(exc),
            )

        self._publish(
            "run.completed",
            session_id,
            thread_id,
            resolved_run_id,
            {"answer": answer},
        )
        return RunResult(resolved_run_id, "completed", answer=answer)

    def _publish(
        self,
        event_type: str,
        session_id: str,
        thread_id: str,
        run_id: str,
        data: dict,
    ) -> None:
        self.bus.publish(
            RuntimeEvent(
                event_type,
                session_id,
                thread_id,
                run_id,
                data,
            )
        )
```

不要在 Runner 里复制 Agent Loop。Runner 管生命周期，Agent 管模型—工具循环。

## 16.6 第四阶段：Session、Thread、Run、Notes 分层

### 16.6.1 要解决的问题

当前 `session.py` 只把 `messages` 保存为 `<session-id>.json`。它无法回答：

- 一次 Session 中有多少次 Run；
- 压缩前后的 Thread 如何关联；
- 子 Agent 属于哪个父任务；
- 哪些事实必须跨压缩保留；
- 某次失败发生在哪个 Run。

### 16.6.2 解决方案

采用四层状态：

```text
Session：长期工作空间
Thread：一条上下文消息链
Run：一次目标执行
Notes：跨 Thread 保留的稳定事实
```

### 16.6.3 新建完整 `mini_claude/runtime/state.py`

```python
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any
from uuid import uuid4


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class SessionState:
    session_id: str
    project_root: str
    created_at: str = field(default_factory=utc_now)
    active_thread_id: str = ""


@dataclass
class ThreadState:
    thread_id: str
    session_id: str
    parent_thread_id: str | None = None
    created_at: str = field(default_factory=utc_now)
    messages: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class RunState:
    run_id: str
    session_id: str
    thread_id: str
    goal: str
    status: str = "pending"
    created_at: str = field(default_factory=utc_now)
    error: str = ""


class SessionStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def create_session(self, project_root: Path) -> SessionState:
        session_id = uuid4().hex
        thread_id = uuid4().hex
        session = SessionState(
            session_id=session_id,
            project_root=str(project_root.resolve()),
            active_thread_id=thread_id,
        )
        thread = ThreadState(thread_id, session_id)
        self.save_session(session)
        self.save_thread(thread)
        self.notes_file(session_id).write_text("", encoding="utf-8")
        return session

    def session_dir(self, session_id: str) -> Path:
        return self.root / "sessions" / session_id

    def notes_file(self, session_id: str) -> Path:
        path = self.session_dir(session_id) / "notes.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def save_session(self, state: SessionState) -> None:
        path = self.session_dir(state.session_id) / "session.json"
        self._write_json(path, asdict(state))

    def save_thread(self, state: ThreadState) -> None:
        path = (
            self.session_dir(state.session_id)
            / "threads"
            / f"{state.thread_id}.json"
        )
        self._write_json(path, asdict(state))

    def save_run(self, state: RunState) -> None:
        path = (
            self.session_dir(state.session_id)
            / "runs"
            / f"{state.run_id}.json"
        )
        self._write_json(path, asdict(state))

    def append_note(self, session_id: str, text: str) -> None:
        with self.notes_file(session_id).open("a", encoding="utf-8") as file:
            file.write(text.rstrip() + "\n")

    @staticmethod
    def _write_json(path: Path, value: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(value, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(path)
```

使用临时文件再替换，避免进程中断后留下半个 JSON 文件。旧 `session.py` 暂时保留为迁移读取器，等新 Session 恢复测试通过后再删除旧写入路径。

### 16.6.4 保存事件和 Trace

新建 `mini_claude/runtime/recorders.py`：

```python
from __future__ import annotations

import json
from pathlib import Path

from mini_claude.runtime.events import RuntimeEvent


class JsonlEventRecorder:
    def __init__(self, runtime_root: Path) -> None:
        self.runtime_root = runtime_root

    def __call__(self, event: RuntimeEvent) -> None:
        path = (
            self.runtime_root
            / "sessions"
            / event.session_id
            / "events.jsonl"
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as file:
            file.write(
                json.dumps(event.to_dict(), ensure_ascii=False) + "\n"
            )
```

`events.jsonl` 记录用户可见的领域事件。`trace.jsonl` 后续记录更细的模型耗时、重试、Token 和内部调度信息。两者不要混成一个无法理解的大日志。

## 16.7 第五阶段：上下文水位和手动 Compact

### 16.7.1 要解决的问题

第十三章已经有四层上下文处理，但运行时还需要把水位变化外化，让客户端知道为什么发生压缩，并允许用户主动执行 `/compact`。

### 16.7.2 解决方案

给 ContextManager 增加结构化结果：

```python
@dataclass(frozen=True)
class ContextStatus:
    used_tokens: int
    limit_tokens: int
    ratio: float
    level: str  # safe / warning / compact / critical
```

统一水位：

```text
0%  ～ 69%：safe
70% ～ 84%：warning
85% ～ 94%：compact
95% 以上：critical
```

每次模型请求前：

1. 计算 `ContextStatus`；
2. 发布 `context.warning`；
3. 达到 compact 水位时执行压缩；
4. 保存旧 Thread；
5. 创建带 `parent_thread_id` 的新 Thread；
6. 发布 `context.compacted`。

手动 `/compact` 不应由 CLI 自己压缩消息，而应发送 Core 命令，让同一个 ContextManager 执行。

## 16.8 第六阶段：权限审批代理

### 16.8.1 要解决的问题

当前 `_confirm()` 会阻塞执行线程等待终端输入。daemon 没有自己的交互终端，TUI 也无法替它输入。

### 16.8.2 解决方案

权限检查仍由现有 `check_permission()` 决定 `allow/deny/confirm`。新增 `PermissionBroker` 只负责：发布审批事件、等待客户端决策、超时后拒绝。

### 16.8.3 新建完整 `mini_claude/runtime/permission_broker.py`

```python
from __future__ import annotations

from dataclasses import dataclass
from threading import Event, Lock
from uuid import uuid4

from mini_claude.runtime.event_bus import EventBus
from mini_claude.runtime.events import RuntimeEvent


@dataclass
class PendingPermission:
    ready: Event
    allowed: bool = False


class PermissionBroker:
    def __init__(self, bus: EventBus) -> None:
        self.bus = bus
        self._pending: dict[str, PendingPermission] = {}
        self._lock = Lock()

    def request(
        self,
        session_id: str,
        thread_id: str,
        run_id: str,
        tool_name: str,
        arguments: dict,
        message: str,
        timeout: float = 300.0,
    ) -> bool:
        request_id = uuid4().hex
        pending = PendingPermission(Event())
        with self._lock:
            self._pending[request_id] = pending

        self.bus.publish(
            RuntimeEvent(
                "permission.requested",
                session_id,
                thread_id,
                run_id,
                {
                    "request_id": request_id,
                    "tool_name": tool_name,
                    "arguments": arguments,
                    "message": message,
                },
            )
        )
        resolved = pending.ready.wait(timeout)
        with self._lock:
            self._pending.pop(request_id, None)
        return resolved and pending.allowed

    def resolve(self, request_id: str, allowed: bool) -> bool:
        with self._lock:
            pending = self._pending.get(request_id)
            if pending is None:
                return False
            pending.allowed = allowed
            pending.ready.set()
            return True
```

然后把 Agent 中的 `_confirm(permission.message)` 替换为 Broker 请求。不要删除 `check_permission()`；分类策略和等待用户决定是两层不同职责。

## 16.9 第七阶段：JSON-RPC 2.0 over NDJSON

### 16.9.1 要解决的问题

CLI、TUI 和 Core 如果直接传 Python 对象，就只能运行在同一个进程。需要一种边界清晰、可调试、能持续推送事件的协议。

### 16.9.2 解决方案

- 每一行是一个完整 JSON 对象，解决消息边界问题。
- 请求和响应采用 JSON-RPC 2.0。
- Runtime Event 作为服务端通知，不带 `id`。
- 第一版只监听 `127.0.0.1`，不开放公网。

### 16.9.3 新建完整 `mini_claude/runtime/protocol.py`

```python
from __future__ import annotations

import json
from typing import Any


def encode_message(message: dict[str, Any]) -> bytes:
    return (
        json.dumps(message, ensure_ascii=False, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def request_message(
    request_id: str,
    method: str,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": method,
        "params": params or {},
    }


def result_message(request_id: str, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def error_message(
    request_id: str | None,
    code: int,
    message: str,
) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": code, "message": message},
    }


def event_notification(event: dict[str, Any]) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "method": "event",
        "params": event,
    }


def decode_line(line: bytes) -> dict[str, Any]:
    value = json.loads(line.decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError("JSON-RPC message must be an object")
    return value
```

### 16.9.4 第一版 RPC 方法

```text
ping
session.create
session.resume
run.start
run.cancel
context.compact
permission.resolve
event.subscribe
trace.read
```

RPC 参数必须在 Core 边界校验，不能把任意字典直接传入文件系统或工具。

## 16.10 第八阶段：实现 mini-core daemon

### 16.10.1 要解决的问题

只有协议函数还不够，需要常驻进程拥有 Agent、SessionStore、EventBus、PermissionBroker 和 MCP 生命周期。

### 16.10.2 解决方案

新增 `CoreService` 管业务，`CoreServer` 只负责 TCP 和 JSON-RPC。不要把 Agent Loop 写进网络处理函数。

### 16.10.3 CoreService 的职责

新建 `mini_claude/runtime/core.py`，实现以下接口：

```python
class CoreService:
    def create_session(self, project_root: str) -> dict: ...
    def resume_session(self, session_id: str) -> dict: ...
    def start_run(
        self,
        session_id: str,
        thread_id: str,
        goal: str,
    ) -> dict: ...
    def cancel_run(self, run_id: str) -> dict: ...
    def compact_context(self, thread_id: str) -> dict: ...
    def resolve_permission(
        self,
        request_id: str,
        allowed: bool,
    ) -> dict: ...
```

`start_run()` 必须把同步的模型调用放入工作线程，立即返回 `run_id`，不能让 JSON-RPC 请求一直等待整个 Agent 任务完成：

```python
future = self.executor.submit(
    runner.run,
    goal,
    session_id,
    thread_id,
    run_id,
)
self.running[run_id] = future
return {"run_id": run_id, "status": "started"}
```

工作线程完成后由 `run.completed` 或 `run.failed` 通知客户端。

### 16.10.4 CoreServer 的最小启动方式

使用 `asyncio.start_server()` 监听本机：

```python
server = await asyncio.start_server(
    handle_client,
    host="127.0.0.1",
    port=8765,
)
```

每个连接维护：

- 一个读取循环；
- 一个带锁的写入函数；
- 是否订阅事件；
- 断开连接时的取消订阅函数。

EventBus 可能从工作线程发布事件。必须使用：

```python
loop.call_soon_threadsafe(queue.put_nowait, event)
```

把事件转交给网络事件循环，不能从模型线程直接操作 `StreamWriter`。

### 16.10.5 增加 daemon 入口

新建 `mini_core.py`：

```python
import asyncio

from mini_claude.runtime.server import serve


if __name__ == "__main__":
    asyncio.run(serve("127.0.0.1", 8765))
```

阶段验证：

```bat
python mini_core.py
```

另开终端发送 `ping`，必须收到：

```json
{"jsonrpc":"2.0","id":"1","result":{"status":"ok"}}
```

在 `ping` 没有通过前，不接入真实 Agent。

## 16.11 第九阶段：把 CLI 改成客户端

### 16.11.1 要解决的问题

如果新 daemon 已经存在，但 `main.py` 仍直接创建 Agent，就会同时存在“本地执行”和“Core 执行”两条路径，Session、权限和事件结果会不一致。

### 16.11.2 解决方案

迁移完成后，`main.py` 只做四件事：

1. 连接 Core；
2. 创建或恢复 Session；
3. 发送 `run.start`；
4. 接收事件并渲染。

### 16.11.3 CLI 事件渲染规则

```python
def render_event(event: dict) -> None:
    event_type = event.get("type")
    data = event.get("data") or {}

    if event_type == "model.token":
        print(str(data.get("text", "")), end="", flush=True)
    elif event_type == "tool.started":
        print(f"\n-> {data.get('name')}: {data.get('arguments')}")
    elif event_type == "context.warning":
        print(f"\n上下文水位：{data.get('ratio', 0):.0%}")
    elif event_type == "run.failed":
        print(f"\n运行失败：{data.get('error', '')}")
```

收到 `permission.requested` 时，CLI 才调用 `input()`，然后发送 `permission.resolve`。因此终端交互属于客户端，不再属于 Agent。

### 16.11.4 删除旧执行路径

确认 CLI 能通过 Core 完成一次工具任务后：

- 删除 `main.py` 中直接创建 `MINI_CLUE_AGENT` 的路径；
- 删除 Agent 中 `_confirm()`；
- 删除 Agent 中平滑打印逻辑；
- Session 只通过 `SessionStore` 写入。

不要为了兼容永久保留两套入口。Git 历史已经可以保留旧实现。

## 16.12 第十阶段：Trace 和回放

### 16.12.1 要解决的问题

`events.jsonl` 适合解释用户看到的过程，但排查性能和模型问题还需要更细的数据。

### 16.12.2 解决方案

新增 `TraceRecorder`，至少记录：

- 模型请求开始、结束和耗时；
- 模型名、finish reason 和 usage；
- 重试次数和错误类型；
- 工具排队、开始、结束和耗时；
- 权限等待时间；
- 压缩前后 Token；
- 子 Agent 的父子 Run ID。

敏感字段不能进入 Trace：

- API Key；
- Authorization Header；
- `.env` 内容；
- 被工具标记为 secret 的参数。

回放器逐行读取 `events.jsonl`，按照 `sequence` 输出。默认不按真实时间等待；增加 `--realtime` 时才根据相邻事件时间差播放。

## 16.13 第十一阶段：结构化 Task 规划

### 16.13.1 要解决的问题

当前 Plan Mode 输出一段计划文字，模型之后是否按计划执行无法检查。复杂任务也无法展示每个步骤的状态。

### 16.13.2 解决方案

建立 Task 数据模型，并把任务操作注册为工具：

```python
@dataclass
class Task:
    task_id: str
    title: str
    description: str
    status: str = "pending"
    depends_on: list[str] = field(default_factory=list)
    assigned_agent: str | None = None
```

工具包括：

```text
task_create
task_update
task_list
```

状态只允许：

```text
pending → in_progress → completed
                      → failed
```

`task_update` 每次改变状态都发布 `task.updated`。任务有未完成依赖时，不允许进入 `in_progress`。

### 16.13.3 为什么不用模型自由书写状态

任务标题和描述可以由模型生成，但 ID、状态迁移、依赖检查和持久化必须由程序控制。否则模型可能重复完成任务、跳过依赖或伪造已经执行的步骤。

## 16.14 第十二阶段：动态子 Agent 编排

### 16.14.1 要解决的问题

第十三章已有多种 `AgentSpec`，但主 Agent 仍需要统一管理子 Agent 的并发、权限、深度和结果回传。

### 16.14.2 解决方案

新增 `SubagentCoordinator`：

- 模型选择已有 `AgentSpec`，不能动态执行任意 Python 代码；
- 每个子 Agent 使用独立 Thread 和 Run；
- 默认最大并发数为 3；
- 默认递归深度为 1；
- 子 Agent 不继承父 Agent 的临时权限批准；
- 父 Agent 只接收结构化摘要；
- 子 Agent 的完整过程进入自己的 events/trace。

可并行条件：

```python
ready = [
    task
    for task in tasks
    if task.status == "pending"
    and all(task_by_id[item].status == "completed"
            for item in task.depends_on)
]
```

不要让多个子 Agent 同时编辑同一个工作区。第一版只允许只读并行；需要写入的子 Agent 顺序执行，后续再考虑 worktree 隔离。

## 16.15 第十三阶段：Research Mode

### 16.15.1 要解决的问题

普通 Coding Agent 擅长围绕一个代码目标循环，但多来源研究需要规划、并行收集、证据核对、反思和补充搜索。把所有内容塞进主 Agent 会快速占满上下文。

### 16.15.2 解决方案

Research Mode 组合 Plan-and-Solve、动态子 Agent 和有限 Reflection：

```text
明确研究问题
→ 生成 Task DAG
→ 文献、本地资料、网络来源分别调查
→ 并行返回结构化证据
→ 汇总并检查冲突
→ Reflection 查找缺口
→ 最多补充两轮
→ 生成带来源和局限性的报告
```

统一子 Agent 返回格式：

```python
@dataclass
class Evidence:
    claim: str
    source: str
    excerpt: str
    confidence: float


@dataclass
class ResearchResult:
    summary: str
    evidence: list[Evidence]
    gaps: list[str]
```

Reflection 只判断：

- 是否回答原问题；
- 是否有无来源的重要结论；
- 来源是否冲突；
- 是否存在时效性问题；
- 是否值得再研究一轮。

程序限制 `max_reflections=2`。达到限制后必须输出当前结论和局限，不能无限循环。

Research Mode 默认只读。需要把报告写入文件时，仍然经过现有权限策略和 PermissionBroker。

## 16.16 第十四阶段：TUI

### 16.16.1 要解决的问题

CLI 能验证协议，但大量事件、Task、子 Agent 和上下文水位会让普通终端输出难以阅读。

### 16.16.2 解决方案

TUI 是另一个 JSON-RPC 客户端，不导入 Agent、ToolRegistry 或 SessionStore。建议使用 Textual：

```bat
pip install textual
```

第一版只实现：

- 对话输出区；
- 输入框；
- 工具调用折叠区；
- 权限审批按钮；
- Task 列表；
- 上下文水位；
- Run 状态。

TUI 崩溃后重新连接 Core，使用 `session.resume` 和 `event.subscribe` 恢复显示。不要把未完成审批默认当成允许；客户端断开时审批保持等待，超时后拒绝。

## 16.17 第十五阶段：工程质量

### 16.17.1 要解决的问题

系统拆成多线程、事件和 IPC 后，只测试单个工具已不够。很多错误只会出现在事件顺序、恢复和并发边界。

### 16.17.2 解决方案

增加开发依赖：

```bat
pip install pytest pytest-asyncio mypy ruff
```

最低测试集合：

```text
tests/test_event_bus.py
tests/test_runner.py
tests/test_session_store.py
tests/test_permission_broker.py
tests/test_protocol.py
tests/test_core_rpc.py
tests/test_trace_replay.py
tests/test_task_state.py
tests/test_subagent_coordinator.py
tests/test_research_mode.py
```

必须覆盖：

1. Event sequence 单调递增。
2. 订阅器异常不会终止 Run。
3. JSONL 半行不会被当作完整消息。
4. 无效 RPC 参数不能进入 Agent。
5. 权限等待超时后默认拒绝。
6. 客户端断开不终止已启动 Run。
7. Core 重启后可以读取 Session、Thread、Run 和 Notes。
8. Trace 不包含 API Key。
9. 子 Agent 不继承写权限。
10. Reflection 达到上限后停止。

统一检查：

```bat
ruff check .
mypy --strict mini_claude
pytest -q
```

## 16.18 最终目录

本章完成后，在第十三章结构基础上新增：

```text
myclaude/
├── main.py                       # CLI 客户端入口
├── mini_core.py                  # Core daemon 入口
├── mini_tui.py                   # TUI 客户端入口
├── mini_claude/
│   ├── agent.py                  # Agent Loop，不负责 UI
│   ├── runtime/
│   │   ├── __init__.py
│   │   ├── events.py
│   │   ├── event_bus.py
│   │   ├── runner.py
│   │   ├── state.py
│   │   ├── recorders.py
│   │   ├── permission_broker.py
│   │   ├── protocol.py
│   │   ├── core.py
│   │   ├── server.py
│   │   ├── client.py
│   │   ├── tasks.py
│   │   ├── subagent_coordinator.py
│   │   ├── research.py
│   │   └── trace.py
│   └── tools/
│       └── task_tools.py
└── tests/
    ├── test_event_bus.py
    ├── test_runner.py
    ├── test_session_store.py
    ├── test_permission_broker.py
    ├── test_protocol.py
    └── test_core_rpc.py
```

## 16.19 完整验收流程

### 16.19.1 启动 Core

```bat
python mini_core.py
```

### 16.19.2 启动 CLI

```bat
python main.py
```

输入：

```text
读取 README.md，总结项目功能，然后在 notes/summary.md 写入摘要
```

预期过程：

1. CLI 发送 `run.start`。
2. Core 返回 `run_id`。
3. CLI 通过事件显示模型 Token。
4. `read_file` 自动执行。
5. `write_file` 发布权限申请。
6. CLI 回传审批结果。
7. Core 继续执行，不在 Agent 内读取终端输入。
8. `events.jsonl` 和 `trace.jsonl` 留下记录。

### 16.19.3 验证客户端与任务解耦

启动较长任务后关闭 CLI，再重新启动并恢复同一 Session。正在 Core 中运行的任务不应因为 CLI 退出而自动失败。

### 16.19.4 验证 Research Mode

```text
研究当前项目与 KamaClaude 的架构差异，要求读取本地代码并核对公开资料。
```

预期：

- 生成结构化 Task；
- 独立只读任务并行执行；
- 每个子 Agent 有独立 Run；
- 父 Agent 接收摘要而不是完整内部历史；
- Reflection 最多两轮；
- 最终结果包含来源和局限。

## 16.20 理解检查

1. 为什么 EventBus 不能只是对 `print()` 的包装？
2. 为什么 AgentRunner 和 Agent Loop 需要分开？
3. 为什么权限分类仍然使用 `check_permission()`，而不是全部交给模型？
4. Session、Thread 和 Run 分别解决什么问题？
5. 为什么 events 和 trace 要分开保存？
6. 为什么客户端断开不应该默认取消 Run？
7. 为什么子 Agent 的临时权限不能继承父 Agent？
8. 为什么 Research Mode 必须限制 Reflection 次数？

## 16.21 本章完成标准

只有同时满足以下条件，才算完成第十六章：

- CLI 和 TUI 都不直接创建 Agent。
- Core daemon 是唯一执行 Agent Run 的进程。
- Agent Loop 不再直接使用 `print()` 和 `input()`。
- 模型、工具、权限、上下文和 Run 状态均产生结构化事件。
- Session、Thread、Run 和 Notes 可以独立恢复。
- events.jsonl 可以回放，trace 可以定位耗时和错误。
- 权限通过 IPC 审批，超时默认拒绝。
- Task 状态由程序校验，不由模型随意改写。
- 子 Agent 有独立上下文、受限工具和递归限制。
- Research Mode 能完成 Plan-and-Solve、并行调查和有限 Reflection。
- `ruff`、`mypy --strict` 和全部 `pytest` 测试通过。

完成这一章后，项目的定位才从“可以调用工具的 Mini-Claude 脚本”升级为“具有运行时边界、事件系统、会话治理和多客户端能力的本地 Coding Agent”。
