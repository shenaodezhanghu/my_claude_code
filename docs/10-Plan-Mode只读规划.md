# 第十章 Plan Mode：只读规划模式

> 本章从第九章最终代码继续。Plan Mode 不是新 Agent，而是同一个 `MINI_CLUE_AGENT` 的一种运行状态。它复用第六章权限门，并在进入工具执行前阻止写文件、编辑文件和运行 Shell。

## 10.1 为什么需要 Plan Mode

复杂任务不应该一开始就修改代码。用户可能希望 Agent 先读取项目、分析影响并给出计划，批准后再实现。

```text
default 模式 → 按第六章权限规则执行
plan 模式    → 允许只读工具，拒绝 write_file / edit_file / run_shell
```

安全限制必须由程序执行，不能只在 System Prompt 中要求模型“不要修改”。

## 10.2 修改 permissions.py

增加：

```python
PLAN_BLOCKED_TOOLS = {
    "write_file",
    "edit_file",
    "run_shell",
}
```

把 `check_permission()` 的签名替换为：

```python
def check_permission(
    tool_name: str,
    arguments: dict,
    mode: str = "default",
    agent_mode: str = "default",
) -> PermissionResult:
```

在函数最前面增加：

```python
if agent_mode == "plan" and tool_name in PLAN_BLOCKED_TOOLS:
    return PermissionResult(
        "deny",
        f"Plan Mode 禁止执行 {tool_name}",
    )
```

后面的第六章权限逻辑全部保留。这里只增加一个更早的只读判断，不要在 Agent 中复制另一套工具名单。

## 10.3 修改 MINI_CLUE_AGENT

在 `__init__()` 中增加：

```python
self.mode = "default"
```

增加：

```python
def set_mode(self, mode: str) -> None:
    if mode not in {"default", "plan"}:
        raise ValueError(f"不支持的 Agent 模式：{mode}")
    self.mode = mode
```

把权限调用替换为：

```python
permission = check_permission(
    name,
    arguments,
    self.permission_mode,
    self.mode,
)
```

不要保留两次 `check_permission()`。Plan Mode 和普通权限由同一个结果对象决定。

## 10.4 把模式写入 System Prompt

在 `MINI_CLUE_AGENT` 中增加：

```python
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
```

在第八章 `_call_model_stream()` 中把 System Prompt 组装替换为：

```python
system_prompt = build_system_prompt()
system_prompt += self._mode_prompt()
system_prompt += recall_memories(
    user_text,
    self.tool_context.project_root,
)
```

仍然只有一个 System Prompt 字符串。

## 10.5 增加 --plan 参数

在 `parse_args()` 中增加：

```python
parser.add_argument(
    "--plan",
    action="store_true",
    help="以只读规划模式运行",
)
```

创建 Agent 后增加：

```python
if args.plan:
    agent.set_mode("plan")
    print("已进入 Plan Mode：只读，不会修改文件或运行 Shell。")
```

`--plan` 是启动参数，不要把它拼入用户 prompt。

## 10.6 验证

运行：

```bat
python main.py --plan "在 notes/plan.txt 中写一份实现计划"
```

确认 Agent 可以读取和搜索项目，但三个修改类工具均被拒绝，并且目标文件没有创建。随后用默认模式执行同一任务，确认恢复第六章的用户确认流程。

## 10.7 理解检查

1. 为什么 Plan Mode 既要进入 System Prompt，又要进入权限代码？
2. `self.mode` 与 `self.permission_mode` 分别控制什么？
3. 为什么本章完全禁用 Shell，而不是维护一份“看起来只读”的命令白名单？
4. Plan Mode 下模型请求 `write_file` 时，为什么应返回 deny 而不是弹出确认框？
5. 从 Plan Mode 回到 default 后，哪些状态应该恢复，哪些会话历史应该保留？

## 10.8 本章最终状态

```text
CLI --plan
→ agent.set_mode("plan")
→ System Prompt 告知只读规划
→ check_permission(..., agent_mode="plan")
→ 写入、编辑、Shell 被程序拒绝
→ 其余能力继续使用同一个 Agent Loop
```

Skills、Session、Memory 和上下文压缩继续工作。下一章会把只读探索委托给拥有独立上下文的子 Agent。
