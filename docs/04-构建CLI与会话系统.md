# 第四章 构建 CLI 与会话系统

> 当前代码进度：第一章使用的 `main.py` 已经实现基础 REPL，可以连续对话；会话保存、`--resume` 和 `/clear` 尚未实现。本章是在现有 REPL 上继续扩展，不改变 `ToolRegistry` 和 Agent Loop。

前三章已经能够在同一进程中连续对话，但关闭程序后消息历史会消失。本章将为现有入口增加单次运行模式、清空历史、保存会话和恢复会话。

## 4.1 本章目标

<strong>（1）支持单次任务与交互式 REPL 两种运行方式</strong>。

<strong>（2）把消息历史持久化为 JSON 文件</strong>。

<strong>（3）实现 `--resume` 与 `/clear`</strong>。

<strong>（4）理解“模型上下文”与“终端显示记录”的区别</strong>。

<strong>（5）理解外层对话循环与内层 Agent Loop 的区别</strong>。

<strong>（6）保证用户输入串行执行，避免多个任务同时修改消息历史</strong>。

## 4.2 CLI 架构

```text
命令行参数
├── 有 prompt → 单次运行 → 保存 → 退出
└── 无 prompt → REPL 循环
                 ├── 普通文本 → agent.chat
                 ├── /clear → 清空历史
                 └── exit → 保存并退出

--resume → 启动时加载上一次 messages
```

这里继续沿用第一章已经建立的两个循环：

```text
外层 REPL
→ 不断等待用户输入

内层 Agent Loop
→ 一次用户任务中不断调用模型和工具
```

第一章已经把 Agent 创建在外层 REPL 之前；本章只扩展命令处理和持久化，不能把 Agent 移回循环内部。

## 4.3 实现会话存储

创建 `mini_claude/session.py`：

```python
import json
from pathlib import Path


SESSION_DIR = Path.home() / ".mini-agent"
SESSION_FILE = SESSION_DIR / "last-session.json"


def save_session(messages: list[dict]) -> None:
    SESSION_DIR.mkdir(parents=True, exist_ok=True)
    SESSION_FILE.write_text(
        json.dumps(messages, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def load_session() -> list[dict]:
    if not SESSION_FILE.exists():
        return []

    try:
        value = json.loads(SESSION_FILE.read_text(encoding="utf-8"))
        return value if isinstance(value, list) else []
    except (OSError, json.JSONDecodeError):
        return []
```

JSON 只能直接保存字典、列表、字符串、数字、布尔值和 `None`。因此 Agent Loop 中必须把 SDK 消息对象转换成普通字典：

```python
self.messages.append(
    message.model_dump(exclude_none=True)
)
```

如果误把 `Agent` 实例或 SDK 对象直接传给 `json.dumps()`，就会出现 `is not JSON serializable`。

为了让 CLI 操作 Agent 历史，在 `MINI_CLUE_AGENT` 中添加：

```python
def history(self) -> list[dict]:
    return list(self.messages)

def load_history(self, messages: list[dict]) -> None:
    self.messages = list(messages)

def clear_history(self) -> None:
    self.messages = []
```

## 4.4 实现命令行入口

重新编写 `main.py`：

```python
import argparse

from dotenv import load_dotenv

from mini_claude.agent import MINI_CLUE_AGENT
from mini_claude.session import load_session, save_session


load_dotenv()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="一个从零实现的 Coding Agent")
    parser.add_argument("prompt", nargs="*", help="要交给 Agent 的任务")
    parser.add_argument("--resume", action="store_true", help="恢复上一次会话")
    return parser.parse_args()


def run_one_shot(agent: MINI_CLUE_AGENT, prompt: str) -> None:
    answer = agent.chat(prompt)
    print(answer)
    save_session(agent.history())


def run_repl(agent: MINI_CLUE_AGENT) -> None:
    print("mini-agent：输入任务，/clear 清空历史，exit 退出。")

    while True:
        try:
            line = input("你：").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if not line:
            continue
        if line in {"exit", "quit"}:
            break
        if line == "/clear":
            agent.clear_history()
            save_session(agent.history())
            print("历史已清空。")
            continue

        answer = agent.chat(line)
        print(f"助手：{answer}")
        save_session(agent.history())


def main() -> None:
    args = parse_args()
    agent = MINI_CLUE_AGENT()

    if args.resume:
        history = load_session()
        agent.load_history(history)
        print(f"已恢复 {len(history)} 条消息。")

    prompt = " ".join(args.prompt).strip()
    if prompt:
        run_one_shot(agent, prompt)
    else:
        run_repl(agent)


if __name__ == "__main__":
    main()
```

Python 使用标准库 `argparse`，不需要额外依赖。后续章节增加 `--model`、`--api-base`、`--plan`、`--max-turns` 时，可以继续在这里扩展，而不必手工解析字符串。

### 4.4.1 模型参数的优先级

建议让命令行参数覆盖环境变量：

```python
parser.add_argument("--model", "-m", default=None)

model = (
    args.model
    or os.environ.get("MINI_CLAUDE_MODEL")
    or "qwen-plus"
)
```

这样可以临时测试其他百炼模型，而不用修改 `.env`。

## 4.5 运行效果

交互模式：

```bat
python main.py
```

单次运行：

```bat
python main.py "读取 pyproject.toml 并解释依赖"
```

恢复上一次对话：

```bat
python main.py --resume
```

交互模式中清空历史：

```text
你：/clear
历史已清空。
```

连续对话成立的关键是 Agent 只创建一次：

```python
agent = MINI_CLUE_AGENT()

while True:
    prompt = input("你：")
    answer = agent.chat(prompt)
```

不要把 `agent = MINI_CLUE_AGENT()` 放进循环，否则每轮都会得到一个空的 `messages`，模型无法记住上一轮。

### 4.5.1 Ctrl+C 和异常处理

基础版可以先让 Ctrl+C 退出：

```python
try:
    line = input("你：").strip()
except (EOFError, KeyboardInterrupt):
    print("\n再见！")
    break
```

完整项目还会区分两种状态：Agent 正在执行时按 Ctrl+C 只中断当前任务；空闲时再次按下才退出程序。这需要异步任务取消，后面配合流式输出再实现。

## 4.6 为什么保存原始 messages

只保存终端中显示的文本是不够的。工具调用链还包含：

- assistant 发出的 `tool_calls`
- 每个调用的唯一 ID
- `role=tool` 的执行结果

恢复会话时缺少其中任何一部分，都可能让消息序列不合法。因此会话文件应该保存模型实际使用的完整消息历史，而不是单纯的聊天记录。

显示给人的终端内容可以截断或美化，但传给模型和保存到会话的消息必须保留协议结构：

```text
终端显示
→ 为人类可读，可以只显示工具摘要

messages
→ 为模型和 API 使用，必须保存完整 tool_calls/tool_call_id
```

### 4.6.1 整体 JSON 与 JSONL

本章使用一个 JSON 文件覆盖保存，代码最简单。但对话很长后有两个问题：每次都重写整个文件，写入中途崩溃还可能损坏全部记录。

生产级实现通常使用 JSONL：

```text
每轮追加一行 JSON
→ 写入接近 O(1)
→ 崩溃最多损坏最后一行
→ 恢复时逐行读取并跳过不完整末行
```

教学阶段先掌握完整消息持久化，之后再把存储格式升级为 JSONL。

## 4.7 理解检查

1. 为什么保存 Session 前要把 SDK Message 转成普通字典？
2. 为什么每次新会话要生成唯一 ID，而不能始终覆盖 `last-session.json`？
3. 为什么恢复历史必须包含 assistant 的 `tool_calls` 和对应 tool 消息？
4. 为什么 REPL 生命周期内只能创建一次 Agent，而不是每轮输入重新创建？
5. Session 保存与第七章模型上下文压缩有什么区别？

## 4.8 本章练习

1. 增加 `--new` 参数，明确创建新会话。
2. 不再只保存 `last-session.json`，而是为每次会话生成唯一 ID。
3. 增加 `/history` 命令，只显示角色和内容摘要。
4. 故意破坏 JSON 文件，验证程序是否能安全回退为空历史。

## 4.9 本章小结

本章在已有连续对话入口上增加了命令参数和会话持久化。只有你真正写完并验证本章代码后，才能把会话系统标记为已完成；当前仓库仍保持“基础 REPL 已有、保存与恢复待实现”的实际状态。
