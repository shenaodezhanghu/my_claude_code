# Day1

## 今日完成

- 完成基础 Agent Loop，并接入 `ToolRegistry` 和 `ToolContext`。
- 实现文件读取、写入、编辑、文件列表、内容搜索、Shell、网页读取和联网搜索等工具。
- 使用 `read-before-edit` 和文件修改时间检查，降低覆盖外部修改的风险。

## 遇到的问题/修改

### 1. 模型读取了任务之外的 `main.py`

任务是“在 `test_tools.py` 中新增 `calculate_sum` 函数”，但模型先后读取了 `AGENTS.md`、目标文件、相似文件以及 `main.py`。

原因：项目中存在 `test_tools.py`，但是不在运行和子级文件夹中，于是他自己创建了一个在运行目录中，忽略了 `mini_claude/tools/test_tool.py`。模型为了判断目标文件、项目结构和运行入口继续进行了探索。读取 `main.py` 是因为在prompt中添加了“修改前先理解相关代码和现有风格。”。

优化：可以优化 System Prompt：当用户已经明确目标文件时，优先读取目标文件及其直接依赖；如果文件不存在，应先说明歧义或选择最接近的文件，避免无关地读取入口文件。

### 2. `run_shell` 出现 GBK 解码错误

执行 Python 文件时出现：

```text
UnicodeDecodeError: 'gbk' codec can't decode byte ...
```

原因：Windows 子进程默认使用 GBK 解码输出，而被执行程序输出了 GBK 无法解析的字节，导致 `subprocess` 的输出读取线程报错。

工具优化：在 `RunShellTool` 的 `subprocess.run()` 中明确指定输出编码和错误处理：

```python
text=True,
encoding="utf-8",
errors="replace",
```

这样即使输出中包含异常字节，工具也不会因为解码失败而中断。

### 3. 流式文字成块出现，不够平滑

背景：第五章接入流式输出后，程序会在收到 `delta.content` 时立即打印模型返回的内容。

问题：模型返回的 `delta.content` 本身是不等长的文本块。直接打印整个 chunk 时，终端中的文字会一片一片出现，而不是平滑显示。

优化方法：在 Agent 中增加平滑输出方法，将每个文本块拆成字符依次显示：

```python
def print_smooth(
    self,
    text: str,
    delay: float = 0.01,
) -> None:
    for char in text:
        print(char, end="", flush=True)
        time.sleep(delay)
```

收到流式文本后调用：

```python
self.print_smooth(delta.content)
```

这种方式改善了终端显示效果，但逐字符等待会轻微降低整体输出速度。后续可以使用“接收队列 + 独立输出任务”，避免阻塞流的接收。

### 4. 删除深层文件时没有先搜索

背景：用户要求删除 `test_tool.py`，但只提供了文件名，没有提供完整相对路径。实际文件位于 `mini_claude/tools/test_tool.py`。

问题：模型只在项目根目录检查 `test_tool.py`，随后认为文件不存在，没有使用 `list_files` 搜索整个项目，因此遗漏了深层目录中的同名文件。

优化方法：在 System Prompt 中明确文件定位流程：

```text
当用户只提供文件名、没有提供完整相对路径时，
必须先调用 list_files(pattern="**/文件名") 搜索整个项目。

只有找到唯一目标路径后才能执行操作；
如果存在多个同名文件，必须先让用户确认。
```

同时优化 `run_shell` 的描述，强调文件定位优先使用 `list_files`，不要使用 Shell 的 `dir`、`find` 或 `Get-ChildItem` 代替已有专用工具。删除属于破坏性操作，确定目标路径后仍应经过权限确认。

# Day2

## 今日完成

- 完成大体量工具结果的落盘保存、结果规范化与上下文压缩，避免完整工具输出长期占用对话上下文。
- 增加项目记忆召回与 Skill 加载能力，让 Agent 能按当前任务补充相关记忆并展开本地 Skill 指令。
- 实现 Plan Mode 的只读权限约束，并增加只读子 Agent 工具，为后续任务拆分保留扩展入口。
- 将 MCP 接入改为可配置的通用客户端，并通过工具注册表动态暴露 MCP 工具，不再依赖单一演示服务器。
- 补充 MCP 配置相关测试，并继续完善会话恢复、项目指令读取和工具权限控制。
- 推进第 13 章至 13.6.2：在 `model.py` 中增加 `ModelCapabilities`，描述显式缓存、工具流完成事件和流式 usage 等模型能力；Agent 与 Prompt 的能力接入留待下一步完成。

## 遇到的问题/规划

### 1. 普通 Coding Agent 难以完成多来源、长流程的研究任务[还未实现]

背景：当前 Agent 主要面向代码任务，虽然已经规划了 Plan Mode 和只读子 Agent，但一次复杂研究可能同时需要读取本地文献、搜索网络资料、核对来源并整理结论。只让一个 Agent 顺序完成全部工作，会快速占满主上下文，也容易遗漏研究方向。

问题：预先手写大量固定子 Agent 难以覆盖不同研究主题；普通 Agent Loop 也缺少“先规划、再分发、汇总验证、发现缺口后补充研究”的完整闭环。多个子任务之间还需要区分依赖关系，允许独立任务并行执行，并确保最终结论能够追溯到真实来源。

优化方法：后续新增独立的 `Research Mode`，由研究编排器组合 Plan-and-Solve、多子 Agent 和 Reflection：

```text
明确研究问题
→ 动态生成结构化研究计划
→ 将文献检索、网络搜索、本地资料分析等任务分发给不同子 Agent
→ 并行收集结构化证据
→ 汇总并交叉验证来源
→ Reflection 检查遗漏、矛盾、时效性和证据质量
→ 必要时重新规划并补充搜索
→ 生成带来源和局限性说明的研究报告
```

子 Agent 不需要为每个研究领域单独编写 Python 类，而是复用通用运行器，由模型动态提供任务、角色和受限工具集合。程序负责最大并发数、工具权限、循环次数、Token 预算和递归限制。Research Mode 默认只读；如需把报告写入文件，仍然经过现有权限确认。
