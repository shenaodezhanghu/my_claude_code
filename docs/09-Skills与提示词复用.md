# 第九章 Skills 与提示词复用

> 本章直接从第八章最终代码继续。Skills 不会创建第二套 Agent Loop，也不是普通工具；它只负责把 `/技能名 参数` 展开成一段完整的用户提示词，然后交给现有的 `agent.chat()`。

## 9.1 为什么需要 Skills

提交代码、检查测试、分析错误等任务经常使用相同的操作说明。Skill 把一段可复用提示词保存为 Markdown 文件：

```text
/commit 修复登录错误
→ 找到 .mini-skills/commit.md
→ 读取技能提示词并追加参数
→ 交给原来的 agent.chat()
```

本章实现原教程的最小 Skills：项目级 Markdown 文件、斜杠调用和参数追加。

## 9.2 本章结构

```text
myclaude/myclaude/
├── .mini-skills/
│   └── commit.md
├── main.py
└── mini_claude/
    ├── agent.py       # 保持第八章最终版
    └── skills.py      # 本章新增
```

## 9.3 创建 skills.py

创建 `mini_claude/skills.py`：

```python
from __future__ import annotations

import re
from pathlib import Path


SKILL_NAME_PATTERN = re.compile(r"^[a-zA-Z0-9_-]+$")


def resolve_skill(text: str, project_root: Path) -> str | None:
    if not text.startswith("/"):
        return None

    name, _, rest = text[1:].partition(" ")
    if not SKILL_NAME_PATTERN.fullmatch(name):
        return None

    skill_file = project_root / ".mini-skills" / f"{name}.md"
    if not skill_file.is_file():
        return None

    try:
        prompt = skill_file.read_text(encoding="utf-8").strip()
    except OSError:
        return None

    if not prompt:
        return None

    arguments = rest.strip()
    return f"{prompt}\n\n{arguments}" if arguments else prompt
```

显式传入 `project_root`，避免 PyCharm 和终端 Working Directory 不同时读取到不同位置。技能名只允许字母、数字、`_` 和 `-`，防止通过 `/../` 越出技能目录。

## 9.4 创建第一个 Skill

创建 `.mini-skills/commit.md`：

```markdown
检查当前 Git 变更，理解本次真正完成的内容。
不要修改文件，也不要执行提交。
根据实际变更生成一条简洁的中文 Conventional Commit 消息。
```

输入 `/commit 重点说明权限系统` 时，实际交给 Agent 的是技能正文加上参数，而不是字符串 `/commit`。

## 9.5 接入 main.py

在 `main.py` 顶部增加：

```python
from mini_claude.skills import resolve_skill
```

增加统一解析函数：

```python
def resolve_user_input(agent: MINI_CLUE_AGENT, text: str) -> str:
    return resolve_skill(
        text,
        agent.tool_context.project_root,
    ) or text
```

在 `run_one_shot()` 中，把：

```python
answer = agent.chat(prompt)
```

替换为：

```python
answer = agent.chat(resolve_user_input(agent, prompt))
```

在 `run_repl()` 中，`exit`、`/clear` 等内置命令判断完成以后，把：

```python
agent.chat(line)
```

替换为：

```python
agent.chat(resolve_user_input(agent, line))
```

内置命令必须先处理，否则 `/clear` 可能被误当成同名 Skill。

## 9.6 Skills 为什么不注册进 ToolRegistry

本章的 Skill 是用户主动输入的提示词快捷方式：

```text
用户输入 → resolve_skill → agent.chat
```

Tool 是模型在 Agent Loop 中主动调用的能力：

```text
模型 tool_call → ToolRegistry.execute
```

二者触发者和执行位置不同。把最小 Skill 强行注册成 Tool，会增加另一条执行路径，偏离原教程第九章。

## 9.7 验证

运行：

```bat
python main.py /commit
```

再进入 REPL 测试 `/commit 重点说明上下文压缩`，确认：

1. Agent 收到 Skill 正文，参数追加在正文后。
2. 普通输入仍直接进入 `agent.chat()`。
3. 不存在的 `/unknown` 不崩溃，而是作为普通消息处理。
4. `/clear` 仍执行内置清空逻辑。

## 9.8 理解检查

1. 为什么本章的 Skill 是输入展开器，而不是 ToolRegistry 中的工具？
2. 为什么 `resolve_skill()` 显式接收 `project_root`，而不直接依赖 `Path.cwd()`？
3. 为什么 `/clear` 等内置命令必须在 Skill 解析之前处理？
4. 不存在的 `/unknown` 应报错还是作为普通消息？两种选择各有什么影响？
5. 当前平铺 Markdown Skill 与带 frontmatter、references 和 scripts 的完整 Skill 有什么差别？

## 9.9 本章最终状态

```text
普通消息 → agent.chat()
斜杠 Skill → 展开 Markdown → agent.chat()
模型工具调用 → 原有 ToolRegistry
```

Skills 复用现有 Agent 的记忆、上下文压缩、权限和工具能力，没有产生第二个 Agent 实现。下一章将在权限门上增加 Plan Mode。
