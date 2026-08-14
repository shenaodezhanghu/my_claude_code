# Mini Claude Agent

一个基于阿里云百炼 OpenAI 兼容接口、使用 Python 从零实现的轻量 Coding Agent。

本项目用于学习 Agent 的底层运行机制，自行实现。


## 项目目标

- 理解模型、工具和消息历史如何组成 Agent Loop。
- 从零实现可扩展的工具注册与执行机制。
- 让 Agent 能够读取、搜索、修改和验证项目代码。
- 为文件修改和 Shell 命令建立安全边界。
- 逐步实现上下文管理、长期记忆、Skills、MCP 和自主运行。

## 当前功能

### Agent 核心

- 使用阿里云百炼 OpenAI 兼容接口调用模型。
- 保存连续对话和完整工具调用消息。
- 支持模型多轮调用工具，直到产生最终回答。
- 支持流式响应和终端平滑输出。
- 对可恢复的模型请求错误进行有限重试。

### 工具系统

项目使用统一的 `Tool + ToolRegistry + ToolContext` 架构，目前注册 9 个工具：

| 工具 | 作用 |
|---|---|
| `read_file` | 读取项目内 UTF-8 文件并返回行号 |
| `write_file` | 创建或覆盖文件 |
| `edit_file` | 使用唯一文本匹配完成局部修改 |
| `list_files` | 使用 glob 模式递归查找文件 |
| `grep_search` | 使用正则表达式搜索项目内容 |
| `run_shell` | 在项目根目录执行命令 |
| `web_fetch` | 读取指定 HTTP(S) 页面 |
| `web_search` | 使用 Tavily 搜索互联网 |
| `environment_info` | 获取时间、Python 和系统环境信息 |

文件工具具备以下保护：

- 只能访问当前项目目录。
- 修改已有文件前必须先读取。
- 使用文件修改时间检查外部变更，避免覆盖用户刚修改的内容。

### 权限控制

工具执行前经过统一权限判断，支持：

| 模式 | 行为 |
|---|---|
| `default` | 读取直接允许，文件修改和危险行为要求确认 |
| `accept_edits` | 自动允许文件修改，危险 Shell 仍需确认 |
| `dont_ask` | 非交互模式下拒绝需要确认的行为 |

当前危险 Shell 检查属于教学版静态规则，不等同于操作系统沙箱。运行 Agent 前仍应确认工作目录和 Git 状态。

### 会话管理

- 每次新会话生成唯一 ID。
- 支持按会话 ID 保存和恢复消息历史。
- 会话默认保存在：

```text
C:\Users\当前用户\.mini-agent\<session-id>.json
```

- 支持 `/clear` 清空当前会话历史。

## 项目结构

```text
myclaude/
├── README.md
├── Development_log.md
└── mycode/
    ├── .env
    ├── AGENTS.md
    ├── main.py
    └── mini_claude/
        ├── agent.py
        ├── model.py
        ├── permissions.py
        ├── prompt.py
        ├── retry.py
        ├── session.py
        └── tools/
            ├── base.py
            ├── registry.py
            ├── file_tools.py
            ├── shell_tools.py
            ├── web_tools.py
            └── environment_tools.py
```

核心文件职责：

| 文件 | 职责 |
|---|---|
| `main.py` | CLI 参数、REPL 和会话生命周期 |
| `agent.py` | Agent Loop、流式响应、权限检查和工具回传 |
| `model.py` | 百炼客户端与模型配置 |
| `prompt.py` | 静态规则、项目说明和运行环境 Prompt |
| `permissions.py` | 工具权限策略与危险命令检查 |
| `session.py` | 会话 JSON 保存和恢复 |
| `retry.py` | 可恢复模型错误的退避重试 |
| `tools/registry.py` | 工具注册、Schema 导出和调用分发 |

## 环境要求

- Python 3.10 或更高版本
- 模型 API Key
- 支持 Tool Calling 的百炼模型
- Tavily API Key（仅 `web_search` 需要）

安装依赖：

```bat
pip install openai python-dotenv tavily-python
```

## 环境配置

在 `mycode/.env` 中配置：

```env
OPENAI_API_KEY=你的百炼API-Key
OPENAI_BASE_URL=你的百炼OpenAI兼容地址
MINI_CLAUDE_MODEL=qwen-plus
TAVILY_API_KEY=你的Tavily-Key
```

不要提交 `.env`，也不要在日志、README 或截图中暴露真实 Key。

PyCharm 的 Working directory 应设置为：

```text
claude-code-from-scratch\myclaude\mycode
```

## 运行方式

进入代码目录：

```bat
cd myclaude\mycode
python main.py
```

创建新会话：

```bat
python main.py --new
```

恢复指定会话：

```bat
python main.py --resume <session-id>
```

选择权限模式：

```bat
python main.py --permission-mode default
python main.py --permission-mode accept_edits
python main.py --permission-mode dont_ask
```

单次任务：

```bat
python main.py "读取 main.py 并说明程序入口"
```

## 使用示例

```text
你：读取 notes/hello.txt 并总结内容
你：搜索项目中 ToolRegistry 出现的位置
你：在 notes/new.txt 写入 hello
你：运行 Python 语法检查
```

当模型只知道文件名、不知道完整路径时，应先使用 `list_files` 搜索整个项目。涉及修改和危险命令时，程序权限层会根据当前模式确认或拒绝。

## 当前限制与后续计划

尚未完成：

- 精确的上下文 Token 预算和自动压缩。
- 大工具结果完整持久化。
- 跨会话长期记忆。
- Skills 与渐进式加载。
- Plan Mode。
- Sub-Agent。
- MCP 外部工具。
- 完整测试、评测和可观测性。
- 真正的 Shell AST 分析与操作系统沙箱。
## 更新日志

### 2026-08-13
```
- 完成阿里云百炼 OpenAI 兼容接口接入。
- 完成基础 Agent Loop 和连续对话。
- 建立 `Tool`、`ToolRegistry`、`ToolContext` 工具架构。
- 注册文件、搜索、Shell、网页和环境信息等 9 个工具。
- 为文件工具增加项目路径限制、read-before-edit 和 mtime 检查。
- 实现动态 System Prompt、项目指令查找和 Git 环境信息。
- 实现流式响应、平滑终端输出和有限重试。
- 实现 `default`、`accept_edits`、`dont_ask` 权限模式。
- 实现唯一会话 ID、会话保存与按 ID 恢复。
- 修复 Windows Shell 输出的 UTF-8/GBK 解码问题。
- 优化模型的文件定位流程，要求深层文件先使用 `list_files` 搜索。
- 建立 Python 实现教程与开发问题日志。
```

项目基于 [claude-code-from-scratch](https://github.com/Windy3f3f3f3f/claude-code-from-scratch) 进行学习与扩展，并借鉴了工具注册等 Agent 框架设计思想。
