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
Exception in thread Thread-1 (_readerthread):
Traceback (most recent call last):
  File "C:\Users\86151\.conda\envs\claude-code\Lib\subprocess.py", line 1599, in _readerthread
    buffer.append(fh.read())
                  ^^^^^^^^^
UnicodeDecodeError: 'gbk' codec can't decode byte 0x80 in position 2: illegal multibyte sequence
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

### 5. Agent 对象被写入消息历史，导致 Session 无法序列化

背景：在完善消息历史和 Session 保存时，需要把模型返回的消息转换成普通字典，再交给 `json.dumps()` 保存。

问题：程序出现：

```text
TypeError: Object of type MINI_CLUE_AGENT is not JSON serializable
```

这说明消息历史中混入了 `MINI_CLUE_AGENT` 实例，而不是只包含字符串、数字、列表和字典等 JSON 数据。常见原因是调用方法时参数位置错误，把 `self` 当成消息内容传入，或者直接把 SDK 消息对象、Agent 对象追加到 `self.messages`。

优化方法：进入历史的模型消息统一执行：

```python
self.messages.append(
    message.model_dump(exclude_none=True)
)
```

工具结果也只保存普通字典。Session 保存前可逐条检查 `type(message)`；不能使用 `default=str` 掩盖错误，因为那只会把错误对象转换为无意义字符串，而不会修复消息结构。

### 6. 平滑输出方法收到 Agent 实例，导致对象不可迭代

背景：为了让流式文本逐字符平滑显示，在 Agent 中增加了 `print_smooth()`，并在收到 `delta.content` 后调用。

问题：运行时出现：

```text
Traceback (most recent call last):
  File "mini_claude/agent.py", line 97, in _call_model_stream
    self.print_smooth(delta.content)
  File "mini_claude/agent.py", line 69, in print_smooth
    for char in text:
TypeError: 'MINI_CLUE_AGENT' object is not iterable
```

`for char in text` 实际收到的是 `self`，说明方法定义或绑定方式错误。实例方法如果漏写 `self`，调用 `self.print_smooth(delta.content)` 时，Python 会自动把 Agent 实例传入第一个形参，最终把 Agent 当成文本遍历。

优化方法：实例方法必须把 `self` 放在第一个参数，并限制 `text` 为字符串：

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

调用前还应判断 `delta.content` 是否为非空字符串，不能通过 `str(self)` 等方式掩盖参数绑定错误。

### 7. 恢复会话得到 0 条消息，并且后续内容被保存到新会话

背景：分别使用已有 Session ID 恢复会话：

```text
python main.py --resume 0a654e06caa742d8a0fa136b7decb2cd
已恢复 0 条消息。

python main.py --resume c594b63bac2040d2bab7bdb82ed549d5
已恢复 0 条消息。
```

即使指定 `0a654e06caa742d8a0fa136b7decb2cd`，后续对话仍然被写入了一个新创建的 Session。

问题：恢复和新建会话的控制流没有使用同一个最终 `session_id`。可能存在以下情况：

- Session ID 对应的文件不存在；
- 保存目录与读取目录不一致；
- JSON 损坏后 `load_session()` 安全回退成了 `[]`；
- 在处理 `args.resume` 前先执行了 `session_id = uuid4().hex`；
- 恢复成功后，保存函数仍然使用新生成的 Session ID；
- 启动时对恢复目标执行 `save_session(session_id, [])`，把原历史覆盖为空。

优化方法：先根据参数确定唯一 Session ID，再进入互斥的新建或恢复分支：

```python
if args.resume:
    session_id = args.resume
    history = load_session(session_id)
    agent.load_history(history)
    print(f"已恢复 {len(history)} 条消息。")
else:
    session_id = uuid4().hex
    save_session(session_id, [])
```

后续所有保存操作必须继续使用这个 `session_id`：

```python
run_repl(agent, session_id)
save_session(session_id, agent.history())
```

恢复分支不能重新生成 ID，也不能初始化空历史文件。若加载结果为空，还应区分“文件不存在”“JSON 损坏”和“文件本来就是空历史”，不能只用同一句“已恢复 0 条消息”掩盖原因。

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

# Day3

## 今日完成

- 重构模型与 Prompt 组装：增加 `ModelCapabilities` 和结构化 `PromptParts`，区分静态、动态 Prompt，并根据模型能力决定是否添加显式缓存标记。
- 完成 Deferred Tools 与 `tool_search`：工具注册表能够区分常驻工具和延迟工具，按名称或描述搜索并激活需要的工具，减少每轮请求携带的工具定义。
- 抽离流式响应收集逻辑：新增 `streaming.py`，统一拼接文本、工具调用参数、结束原因和 usage，Agent 不再自行处理底层流式分片。
- 新增工具调度器 `scheduler.py`：只读且声明为并发安全的工具可批量并行执行；写入类或非并发安全工具仍保持顺序执行，并按原工具调用顺序返回结果。
- 新增 Budget 控制：统计模型调用轮数、输入/输出 Token、缓存创建与命中 Token，并支持按最大轮数和估算费用停止 Agent Loop。
- 完善四层上下文压缩：依次限制单个工具结果、裁剪陈旧工具结果、去除重复结果，并在历史超过阈值后将较早对话总结为摘要，同时保留最近对话。
- 增加 Prompt Too Long 恢复机制：上下文超限时先压缩历史，只额外重试一次，避免把确定性错误交给普通网络重试无限重复。
- 推进 MCP 外部工具接入：补充通用 MCP 配置、代理工具注册和关闭连接流程，并完善第 12 章相关文档与配置测试。
- 扩充第 13 章架构文档，整理 Prompt 缓存、Deferred Tools、Streaming、工具调度、Budget、上下文管理、Skills、Plan Mode、Sub-Agent、多 MCP、CLI 与测试的后续实现路线。

## 遇到的问题/修改

### 1. 所有工具定义长期放入 Prompt 会浪费上下文

背景：工具数量增加后，如果每轮都把所有工具 Schema 发送给模型，会持续占用输入 Token，也会削弱模型选择工具的准确性。

优化方法：为工具增加 `deferred` 元数据。默认只暴露核心工具和 `tool_search`；当模型发现缺少能力时，先搜索并激活匹配工具，从下一轮开始再把对应 Schema 提供给模型。

### 2. 工具全部串行执行会拖慢只读任务

背景：同一轮中可能同时出现多个互不依赖的读取、搜索请求，如果全部串行执行，总耗时会累加。

优化方法：新增 `ToolScheduler`，将连续且 `concurrency_safe=True` 的工具组成安全批次并行执行；遇到写入或其他非并发安全工具时，先等待当前批次完成，再顺序执行该工具。最终结果仍按原调用索引排序，保证工具消息协议稳定。

### 3. 长对话不能只依赖一次粗粒度摘要

背景：直接摘要全部历史可能破坏最近的工具调用关系，也可能为了少量超长工具输出而过早调用模型生成摘要。

优化方法：按成本从低到高执行分层压缩：

```text
限制单个工具结果
→ 裁剪陈旧工具结果
→ 合并重复工具结果
→ 总结较早对话并保留最近三轮
```

这样优先通过确定性规则释放空间，只有历史仍然过长时才调用模型摘要。

### 4. Prompt Too Long 不能交给普通网络重试

背景：在模型请求中，如果历史消息和工具结果过长，可能触发上下文超限错误，例如 `prompt too long`、`maximum context length` 或 `context_length_exceeded`。

问题：这类错误不是临时网络错误。普通 `with_retry()` 会重复发送同一份过长上下文，即使重试多次也不会成功，还会浪费请求次数和时间。

优化方法：在 `retry.py` 中增加 `is_prompt_too_long()`，专门识别上下文过长错误。模型调用处捕获该错误后，先执行一次上下文压缩，再额外重试一次模型请求；如果压缩后仍然超限，就把错误交给用户，不能无限摘要和无限重试。

```python
def is_prompt_too_long(exc: Exception) -> bool:
    text = str(exc).lower()
    return any(
        marker in text
        for marker in (
            "prompt too long",
            "maximum context length",
            "context_length_exceeded",
        )
    )
```

核心流程：

```text
模型调用失败
→ 判断是否是 Prompt Too Long
→ 如果不是，继续按普通错误处理
→ 如果是，先压缩上下文
→ 只额外重试一次
→ 仍失败则停止并提示用户
```

# Day4

## 今日完成

- 完成第 13.15 节的单进程 CLI 收尾：将新建与恢复逻辑统一到 `open_session()`，由 `CliApplication` 持有当前 Agent，并为后续会话切换提供统一入口。
- 完成 Session 工作区体系：每个会话绑定独立 `workspace_root`，消息、运行状态、工作记忆和工具结果统一保存到项目内 `.mini-agent/sessions/<session_id>/`。
- 增加 Session Index，通过 `session_id → workspace_root` 恢复原项目；随后将索引从用户主目录迁移到当前 Mini Claude 项目的 `.mini-agent/session-index.json`，并补录已有项目内会话。
- 完成动态目录授权：`WorkspacePolicy` 分别维护可读与可写目录，外部目录首次访问时确认授权，文件工具执行前继续进行最终路径边界检查。
- 完成运行状态持久化与恢复：`state.json` 保存模型、权限模式、Mode、Deferred Tool 激活状态、工作区授权、Budget 使用情况、Plan 和最近一次 usage；损坏的 JSON 明确报错，不再静默恢复为空状态。
- 完成配置优先级：本次显式 CLI 参数优先于 Session 状态，Session 状态优先于环境变量或程序默认值；Token 单价继续由 `main.py` 常量提供，并可从旧 Session 恢复。
- 完成 Slash Command Registry，支持 `/help`、`/status`、`/history`、`/sessions`、`/resume`、`/new`、`/mode`、`/model`、`/cwd`、`/permissions`、`/compact`、`/clear` 和 `/exit`，并保留现有 Skill 的斜杠调用。
- 完成取消链路：Ctrl+C 能通知当前 Agent，模型流、平滑输出、工具调度器和 Shell 共享同一个取消事件；取消异常不会再被 Registry 或 Scheduler 转换成普通工具错误。
- 完成外部目录结果路径兼容：项目内文件显示相对路径，授权的外部文件显示绝对路径，避免 `relative_to(project_root)` 在外部路径上抛出异常。
- 完成 Token 与费用展示：区分普通输入、缓存命中和输出 Token，按各自每百万 Token 单价计算本轮与累计人民币费用。
- 完成进入下一阶段前的整体检查，修正 Agent 公共方法归属、工作区属性引用、`--new/--resume` 参数互斥、取消输出检查、模块导入路径和 Plan 审批选项提示等连接问题。

## 遇到的问题/修改

### 1. 无需执行的 Plan 被再次送入 Agent Loop，界面停在“助手：”

背景：在 Plan Mode 中完成旅游行程等纯文本规划后，程序展示审批菜单。用户选择“2. 保留历史并执行”，终端随后只显示“助手：”，看起来像程序卡住。

问题：选项 2 的语义是“批准并执行 Plan”。`_review_plan()` 会把完整 Plan 重新追加为一条用户消息并返回 `True`，使 `chat()` 继续调用模型。对于已经完成、且明确不涉及代码或工具操作的规划，没有实际执行步骤，因此程序仍然发起了一次没有必要的模型请求，容易被误认为卡死。原菜单缺少“接受规划结果，但不执行”的明确出口。

优化方法：区分“实施计划”和“仅供查看的规划”。为 `_review_plan()` 增加“接受计划并返回对话，但不执行”的选项；该分支退出 Plan Mode、清理审批状态并返回 `False`，把控制权交还 REPL：

```python
if choice == "5":
    self.mode = "default"
    self.plan.active = False
    self.plan.awaiting_review = False
    return False
```

菜单同步增加：

```text
5. 接受计划，返回对话但不执行
```

使用边界：需要修改代码的 Coding Plan 选择 1 或 2；需要继续完善选择 4；旅游路线、研究提纲等已经完成的纯文本计划选择 5。普通问答和旅游规划通常不需要启动 `--plan`。

### 2. 取消事件没有贯穿完整调用链

背景：程序已经能够通过 Ctrl+C 发出取消信号，但模型流式响应、平滑输出、并行工具调度、工具注册表和 Shell 子进程最初分别处理自己的执行过程。

问题：如果这些组件没有共享同一个取消事件，Ctrl+C 可能只停止最外层等待，内部的模型流、线程任务或 Shell 子进程仍会继续运行。同时，Registry 或 Scheduler 如果捕获并包装取消异常，Agent 会把“用户主动取消”误当成普通工具失败，继续进入下一轮模型调用。

优化方法：由 Agent 创建并持有同一个 `threading.Event`，再把它传入模型流收集器、平滑输出、`ToolContext`、`ToolScheduler` 和 Shell 工具。各层在执行前及长任务过程中检查该事件；收到取消后关闭活动 Stream、终止可终止的子进程，并原样向上传递 `AgentCancelled`，不能转换为普通工具结果。新建或恢复下一次任务前，再统一清除取消状态。

### 3. 模型输出本身不能作为安全边界

背景：模型在执行 `git reset --hard`、删除文件或运行 Shell 命令前，通常能够用自然语言说明风险并询问用户是否确认。

问题：模型的提示内容具有不确定性，可能遗漏风险、误判命令，也可能直接发起工具调用。仅依赖 System Prompt 要求模型“谨慎操作”，不能保证危险行为一定被阻止，因此模型生成的警告不能代替程序级权限控制。

优化方法：把安全边界放在工具执行层。工具调用必须先经过工作区路径校验、权限模式判断和危险操作确认；明确禁止的操作直接拒绝，需要审批的操作由程序展示固定确认提示并等待用户输入。Shell 命令还应采用命令结构解析、允许/拒绝规则、运行隔离和最小权限等多层防护。模型负责提出操作和解释原因，程序负责最终授权与执行。

# Day5

## 今日完成

- 将第 15 章评估能力落地到 `evals/` 目录，形成可运行的自建评估框架，包括数据集、fixture 工作区、工具轨迹评分、代码任务评分、评估入口和 JSON 报告输出。
- 扩充自建评估集：工具调用类和代码修改类各 10 条，共 20 条，用于评估工具选择、参数匹配、并行读取、文件修改范围、测试验证、成本和耗时。
- 删除 BFCL 接入代码，将评估重点从“模型函数调用能力”转向 Mini Claude Agent 自身的端到端能力。
- 接入 GAIA 小样本评估入口，支持 `validation/test` split、`limit` 和 `offset`，便于只重跑失败或中断的题目。
- 修复评估入口未加载 `.env` 的问题，使 `run_eval.py` 和 `run_official.py` 能直接读取项目根目录下的百炼配置。
- 完成一次自建 baseline 评估：20 条样例通过 15 条，通过率 75%，记录了总耗时、Token、缓存命中、费用、模型轮次和工具调用数。
- 完成 GAIA 小样本流程测试，并人工分析了金币翻面、字母变化、乒乓球概率和附件题的流程质量与答案正确性。
- 明确第 16 章后续优化方向：减少不必要工具调用、强化代码修改后的测试验证、改进文件创建任务闭环、让评估器区分任务失败和评估规则过严。

## 遇到的问题/修改

### 1. 工具调用可能过多，调用不够精准

背景：运行自建评估集后发现，部分任务虽然最终完成并通过测试，但 Agent 在过程中会多次探索项目结构、重复尝试路径或额外运行命令，导致工具调用数偏高。

问题：工具调用过多会增加耗时、Token 消耗和执行风险，也会让评估结果被 `max_tool_calls` 这类指标判为失败。部分失败并不是任务没有完成，而是工具选择和探索路径不够精准。

优化方法：后续需要在 Prompt、工具描述和评估规则三处同时优化。Prompt 中强调优先使用当前工作区相对路径，工具描述中明确参数语义，评估器中区分“真实失败”和“已完成但调用冗余”。第 16 章优化时可重点统计工具调用数、模型轮次、耗时和成本。

### 2. 有时候修改代码后没有运行测试

背景：Coding Task 评估中，部分样例已经成功修改目标文件，但 Agent 没有继续调用 `run_shell` 运行测试验证。

问题：Coding Agent 不能只完成代码编辑，还必须验证结果。未运行测试会导致隐藏错误无法被发现，也会让评估结果不能证明任务真的完成。

优化方法：在 Coding 类任务的 System Prompt 或任务执行规范中明确：凡是修改代码、配置或测试相关文件，完成后必须运行已有测试或用户指定验证命令；如果不能运行测试，必须说明原因。评估器继续保留 `run_shell` 作为代码任务的必要工具之一。

### 3. 创建文件任务出现真实失败

背景：`code-003` 要求创建 `config/settings.json`，但实际运行时 Agent 只执行了创建目录的 Shell 命令，没有调用 `write_file` 写入目标 JSON 文件。

问题：这是任务执行失败，不是评估规则过严。Agent 把“准备目录”误当成了“完成文件创建”，说明它对文件创建类任务的完成条件判断不稳定。

优化方法：后续应强化文件创建任务的完成闭环：必须使用 `write_file` 生成目标文件，必要时再 `read_file` 验证内容，最后运行验证命令。评估器继续检查 `expected_files` 和 `verify_command`，确保没有文件创建时一定失败。

### 4. 评估规则需要区分“评估失败”和“任务失败”

背景：部分样例最终文件修改正确、测试通过，但因为工具调用数略超限制或工具调用参数存在等价写法，被评估器判为失败。

问题：如果评估器过于死板，会把可接受的实现路径误判为 Agent 能力问题。例如 `list_files` 使用 `pattern="docs/**/*.md"` 与 `pattern="*.md", path="docs"` 在语义上等价，但当前规则只做固定参数匹配。

优化方法：评估结果需要分层记录：功能是否完成、工具轨迹是否理想、成本和耗时是否达标。对于路径、glob、验证命令等允许多种等价实现的地方，应增加归一化或语义等价判断，避免把评估器限制误当成 Agent 缺陷。

# Day6

## 今日完成

- 基于 `baseline-v2` 和 `optimized` 两份报告，对自建评估集和 GAIA 小样本做了结果对比。
- 重写第 16 章教程，把当前代码中没有真正实现的内容删除或改为后续方向。
- 在第 16 章中补充 GAIA 过程对比，区分“结果是否正确”和“解题过程是否更好”。

## 遇到的问题/修改

### 1. Prompt 优化让 GAIA 回答变得教程化

背景：对比 Pick That Ping-Pong 这道 GAIA 题时发现，`baseline-v2` 的回答更短，并额外给出模拟验证；`optimized` 虽然也答对，但推导展开更多，整体更像教程式讲解。

问题：这不是模型能力下降，而是 Prompt 偏好发生了变化。优化后的 Prompt 强调“准确、安全、验证、说明结果和验证情况”，GAIA 适配器又追加“请先完成必要推理”，模型因此更倾向输出完整推导过程。对于 GAIA 这类问答题，过长过程不一定带来更高正确率，反而会增加输出 Token，并削弱答案的简洁性。

优化方法：将 Coding 任务和 GAIA 问答任务的回答策略拆开。代码或文件修改任务继续要求运行测试并说明验证情况；GAIA / 问答 / 纯推理任务只要求做必要自检，最终回答保持简洁。GAIA 的格式提示可以改为：

```text
请完成必要推理，但过程保持简洁。
只写关键依据，不要写长篇教程式推导。
如果适合验证，用一句话说明校验方式。
最后必须单独输出一行：
Final answer: <你的最终短答案>
```

这样既保留 `Final answer` 的可抽取性，也避免把所有 GAIA 题都写成 Coding 任务式汇报。
