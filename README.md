<div align="center">

# Mini Claude Agent

**一个从零实现的轻量级 Coding Agent**

[![Python](https://img.shields.io/badge/Python-3.11+-3776AB?style=flat-square&logo=python&logoColor=white)](#环境要求)
[![Model](https://img.shields.io/badge/Model-OpenAI%20Compatible-10A37F?style=flat-square)](#环境配置)
[![Eval](https://img.shields.io/badge/Eval-GAIA%20%2B%20Local-blue?style=flat-square)](#评估与对比)
[![License](https://img.shields.io/badge/License-Learning%20Project-lightgrey?style=flat-square)](#许可证)

<br/>

[快速开始](#快速开始)
&nbsp;&nbsp;|&nbsp;&nbsp;
[功能特性](#功能特性)
&nbsp;&nbsp;|&nbsp;&nbsp;
[教程目录](#教程目录)

</div>

---

## 项目简介

Mini Claude Agent 是一个使用 Python 从零实现的轻量 Coding Agent。它基于阿里云百炼 OpenAI 兼容接口，完整串起模型调用、工具执行、流式输出、权限控制、上下文管理、长期记忆、Skills、Plan Mode、Sub-Agent、MCP 接入和评估系统。

这个项目不是为了复刻 Claude Code 的内部实现，而是为了把 Coding Agent 的核心机制拆开讲清楚：模型如何决定调用工具、工具结果如何回到上下文、文件修改如何保证安全、长上下文如何压缩、复杂任务如何规划和评估。

适合这些场景：

- 学习 Agent Loop、Tool Calling 和 Coding Agent 的底层实现。
- 研究 Claude Code 类产品的架构拆解方式。
- 搭建一个可控、可调试、可评估的本地 Agent 实验环境。
- 用自建数据集和 GAIA 小样本验证 Prompt 与工具策略优化是否真的有效。

## 功能特性

### Agent 核心

- 支持 OpenAI 兼容接口，默认从 `.env` 读取模型配置。
- 支持多轮 Agent Loop：模型调用工具，工具结果回填，再继续推理。
- 支持流式输出、平滑终端打印、Ctrl+C 取消和有限重试。
- 支持 token、模型轮次和成本预算统计。
- 支持 Prompt Too Long 识别和上下文压缩后重试。

### 权限与安全

支持三种权限模式：

| 模式 | 行为 |
|---|---|
| `default` | 读取直接允许，文件修改和危险行为要求确认 |
| `accept_edits` | 自动允许文件修改，危险 Shell 仍需确认 |
| `dont_ask` | 非交互评估模式，需要确认的行为自动拒绝 |

同时支持工作区授权：

- 默认只允许读写当前项目目录。
- 访问外部目录时需要确认授权。
- Plan Mode 下只允许修改计划文件，不允许修改项目文件或运行 Shell。

> 当前权限系统是教学版安全边界，不等同于操作系统级沙箱。运行真实项目时仍建议先确认 Git 状态。

## 快速开始

### 环境要求

- Python 3.11+
- 支持 Tool Calling 的 OpenAI 兼容模型
- Tavily API Key（仅 `web_search` 需要）

安装依赖：

```bat
cd myclaude\myclaude
pip install openai python-dotenv tavily-python datasets pytest
```

### 环境配置

在 `myclaude/myclaude/.env` 中配置：

```env
OPENAI_API_KEY=你的百炼或 OpenAI 兼容 API Key
OPENAI_BASE_URL=你的 OpenAI 兼容接口地址
MINI_CLAUDE_MODEL=qwen3.7-flash
TAVILY_API_KEY=你的 Tavily Key
```

### 启动 Agent

```bat
python main.py
```

单次任务：

```bat
python main.py "读取 main.py 并说明程序入口"
```

创建新会话：

```bat
python main.py --new
```

恢复指定会话：

```bat
python main.py --resume <session-id>
```

指定权限模式：

```bat
python main.py --permission-mode default
python main.py --permission-mode accept_edits
python main.py --permission-mode dont_ask
```

指定模型、Plan Mode 与工作区：

```bat
python main.py --model qwen3.7-flash
python main.py --plan
python main.py --cwd E:\path\to\your-project
```

## 教程目录

项目文档位于 `myclaude/docs/`，按开发顺序组织：

| 章节 | 内容 |
|---|---|
| 00 | 环境搭建与百炼接入 |
| 01 | 构建最小 Agent Loop |
| 02 | 为 Agent 添加本地工具 |
| 03 | 设计系统提示词 |
| 04 | 构建 CLI 与会话系统 |
| 05 | 流式输出与可靠调用 |
| 06 | 工具权限与安全控制 |
| 07 | 上下文压缩与大结果管理 |
| 08 | 跨会话记忆系统 |
| 09 | Skills 与提示词复用 |
| 10 | Plan Mode 只读规划 |
| 11 | 多 Agent 与只读子 Agent |
| 12 | MCP 外部工具接入 |
| 13 | 架构复盘与下一步 |
| 14 | 功能测试与验收 |
| 15 | Mini Claude Agent 评估系统 |
| 16 | Agent 优化 |


## 项目结构

```text
myclaude/
├── README.md
├── Development_log.md
├── docs/
│   ├── 00-环境搭建与百炼接入.md
│   ├── ...
│   └── 16-Agent 优化.md
└── myclaude/
    ├── main.py
    ├── AGENTS.md
    ├── evals/
    │   ├── datasets/
    │   ├── fixtures/
    │   ├── official/
    │   ├── reports/
    │   ├── run_eval.py
    │   ├── run_official.py
    │   └── compare_reports.py
    ├── mini_claude/
    │   ├── agent.py
    │   ├── budget.py
    │   ├── context.py
    │   ├── mcp_client.py
    │   ├── memory.py
    │   ├── permissions.py
    │   ├── plan.py
    │   ├── prompt.py
    │   ├── prompt_cache.py
    │   ├── scheduler.py
    │   ├── session.py
    │   ├── streaming.py
    │   ├── subagent.py
    │   ├── workspace.py
    │   └── tools/
    └── tests/
```


## 开发计划

- 为 agent 可视化
- 增加 Trace 回放系统，支持复盘一次任务的完整执行链路，定位工具多调用、参数错误和任务失败原因。
- 优化工具调用策略，减少重复读取、无关探索和错误参数调用，并强化“修改后必须验证”的闭环。
- Session / Workspace / Memory 体系完善，新增记忆自动升级
- 强化权限系统，将工具权限、路径权限、危险命令确认和 Plan Mode 只读边界统一纳入运行链路。

## 贡献

欢迎通过 Issue 或 Pull Request 参与改进：

1. 补充新的评估样例或 fixture。
2. 改进工具安全策略和丰富mini-claude能力。
3. 修复 Windows / macOS / Linux 下的兼容性问题。
4. mini-claude可视化界面

如果你要提交代码，请尽量同时补充测试或评估记录。

## 致谢

本项目基于 [claude-code-from-scratch](https://github.com/Windy3f3f3f3f/claude-code-from-scratch) 的学习路线进行扩展，并参考了 Hello Agent 的知识。

感谢所有关于 Agent Loop、Tool Calling、MCP、Skills、Plan Mode 和评估系统的开源实践。

## Star History

如果这个项目对你有帮助，欢迎点一个 Star。

<div align="center">

![Star History Chart](https://api.star-history.com/svg?repos=Windy3f3f3f3f/claude-code-from-scratch&type=Date)

</div>

## 许可证

本项目用于学习和研究。若基于上游仓库继续分发，请同时遵守原仓库许可证。
