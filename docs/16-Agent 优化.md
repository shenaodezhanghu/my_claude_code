# 第十六章 评估驱动的 Agent 优化

第十五章搭好了评估系统，第十六章的目标不是继续堆功能，而是根据评估暴露出来的问题，把 Mini Claude 改得更稳、更省、更容易对比。

本章以当前代码为准，只记录已经落地的优化：

```text
1. 自建评估集：工具调用题 + Coding 题
2. 评估报告：通过率、耗时、token、成本、工具调用、模型轮次
3. Prompt 构建耗时埋点：prompt_build_ms / schema_build_ms / prepare_total_ms
4. Prompt 缓存：复用项目说明和 Git 信息
5. 工具调用优化：工具描述、读写约束、并发安全工具调度
6. GAIA 适配：固定 split/offset/limit、Final answer 抽取、附件状态记录
7. 报告对比：baseline 与 optimized 指标对比
```

没有在当前代码中真正实现的内容，本章不再写成教程步骤。例如：低成本自检、失败时 Reflection、准备阶段并行、GAIA 完整工具轨迹保存，这些可以作为后续优化，但不能写成已经完成。

## 16.1 先确认评估命令

自建数据集使用：

```bat
python -m evals.run_eval --suite all --profile baseline-v2
python -m evals.run_eval --suite all --profile optimized
```

GAIA 使用官方适配器：

```bat
python -m evals.run_official --benchmark gaia --gaia-split validation --limit 3
```

如果要保证两次 GAIA 对比的是同一批题，要固定：

```text
--gaia-split
--gaia-level-config
--offset
--limit
```

默认情况下，这条命令等价于：

```text
split = validation
level_config = 2023_level1
offset = 0
limit = 3
```

所以它每次都会选 validation / 2023_level1 的前 3 条，不会随机换题。模型回答可能有波动，但题目本身固定。

## 16.2 自建评估系统

自建评估入口是：

```text
evals/run_eval.py
```

它会加载两个数据集：

```text
evals/datasets/tool_calling.jsonl
evals/datasets/coding_tasks.jsonl
```

其中：

```text
tool_calling.jsonl：10 条工具调用任务
coding_tasks.jsonl：10 条 Coding 任务
```

每条 case 会准备一个临时 workspace，运行 Agent，然后从 `agent.history()` 中抽取工具调用轨迹，再结合文件变化和验证命令判断是否通过。

评估结果写入：

```text
evals/reports/<时间戳>-<profile>.json
```

这里的 `profile` 只是报告标签，不会自动切换代码版本。也就是说：

```bat
python -m evals.run_eval --suite all --profile baseline-v2
python -m evals.run_eval --suite all --profile optimized
```

评估的都是当前工作区代码。要比较优化前后，必须在代码修改前跑一次 baseline-v2，修改后再跑 optimized。

## 16.3 评估报告记录了什么

`evals/schema.py` 中的 `EvalResult` 记录每条样例的结果：

```text
case_id
category
passed
final_answer
tool_calls
changed_files
duration_seconds
model_turns
total_tool_calls
input_tokens
output_tokens
cache_read_tokens
cache_creation_tokens
estimated_cost_usd
prompt_build_ms
schema_build_ms
prepare_total_ms
errors
```

`evals/run_eval.py` 的 summary 会汇总：

```text
total
passed
pass_rate
duration_seconds
input_tokens
output_tokens
cache_read_tokens
cache_creation_tokens
estimated_cost_usd
model_turns
tool_calls
prompt_build_ms
schema_build_ms
prepare_total_ms
```

这些指标能回答三个问题：

```text
有没有更准：pass_rate / passed / failed
有没有更省：input_tokens / output_tokens / estimated_cost_usd
有没有更快：duration_seconds / prompt_build_ms / prepare_total_ms
```

## 16.4 Prompt 缓存与准备耗时埋点

当前优化新增了：

```text
mini_claude/prompt_cache.py
```

核心结构是 `PromptBuildCache`：

```text
project_instruction：缓存 AGENTS.md / CLAUDE.md 内容
git_context：缓存 git branch / git status
git_dirty：写文件或运行 shell 后标记 Git 信息需要刷新
git_ttl_seconds：Git 信息 TTL，当前为 5 秒
```

`mini_claude/prompt.py` 中的 `build_prompt_parts()` 接收 cache 参数：

```python
prompt_parts = build_prompt_parts(
    project_root=self.tool_context.project_root,
    mode_prompt=self._mode_prompt(),
    memory_prompt="",
    deferred_names=self.tools.deferred_names(),
    cache=self.prompt_cache,
)
```

这样每次模型调用前，不需要无条件重新读取项目说明，也不需要每轮都重新跑 Git 命令。

`mini_claude/agent.py` 中同时记录了三段准备耗时：

```text
prompt_build_ms：构建 system prompt 的耗时
schema_build_ms：构建 tools schema 的耗时
prepare_total_ms：模型请求发出前的总准备耗时
```

写入工具执行后，如果工具是：

```text
write_file
edit_file
run_shell
```

Agent 会调用：

```python
self.prompt_cache.mark_git_dirty()
```

这样下轮 prompt 会刷新 Git 状态，避免缓存造成上下文过期。

## 16.5 工具调用过程优化

第十五章暴露出的典型问题是：任务能完成，但工具调用不够稳。有的任务会先读错路径，有的任务会重复探索，有的任务会用 shell 做已有工具能完成的事情。

当前优化落在三个地方。

第一，`mini_claude/prompt.py` 收紧了工作规则：

```text
修改前先理解相关代码和现有风格
优先进行范围最小、可验证的修改
工具失败最多进行一次相邻修正
执行完任务后必须找方法测试或验证
已有工具能完成任务时，不要用 shell 代替 write_file/edit_file
拿到足够信息时停止工具调用，直接总结
```

第二，文件工具描述更明确：

```text
read_file：读取当前项目中的 UTF-8 文本文件，返回带行号的内容
write_file：创建或覆盖文件；覆盖已有文件前必须先 read_file
edit_file：用 new_text 替换唯一出现的 old_text；编辑前必须先 read_file
list_files：只知道文件名、目录或 glob 模式时用于定位候选文件
grep_search：按文本内容定位文件或符号时使用
```

第三，`edit_file` / `write_file` 加了文件新鲜度检查：

```text
修改已有文件前必须先 read_file
如果文件在读取后被外部修改，要求重新 read_file
```

这让 Agent 更接近真实 Coding Agent 的工作方式：先看，再改，再验证。

## 16.6 并发安全工具调度

当前代码新增了：

```text
mini_claude/scheduler.py
```

`ToolScheduler` 会把同一批工具调用分成两类：

```text
concurrency_safe = True：可以并发执行
concurrency_safe = False：必须串行执行
```

当前读类工具是并发安全的，例如：

```text
read_file
list_files
grep_search
web_fetch
web_search
environment_info
memory_search
working_memory_read
```

写类工具仍然串行执行，例如：

```text
write_file
edit_file
run_shell
```

这样做的好处是：当模型一次性发出多个只读工具调用时，可以并发读取，减少等待；但涉及文件修改或 shell 命令时，仍然保持顺序，避免竞态。

评估器里也支持检查并发批次：

```text
expected_parallel_groups
```

它会判断一组工具是否出现在同一个 assistant tool-call batch 中，并且是否都属于并发安全工具。

## 16.7 工具轨迹评分

工具评分逻辑在：

```text
evals/evaluators/tool_calling.py
```

它会从 `agent.history()` 中抽取：

```text
工具名
参数
调用批次 batch
调用顺序 order
工具结果是否成功
```

然后检查：

```text
必要工具是否调用
禁止工具是否未调用
参数是否匹配
工具顺序是否正确
并发组是否在同一批次
是否重复读取未变化文件
edit_file 前是否先 read_file
deferred tool 是否先经过 tool_search 激活
工具调用总数是否超过 max_tool_calls
```

这里有一个重要边界：当前 `list_files {"pattern": "docs/**/*.md"}` 和 `list_files {"path": "docs", "pattern": "*.md"}` 仍然不会被当成等价参数。也就是说，第 16 章不能写“已经支持 list_files 等价路径归一化”，因为当前代码还没有实现这个匹配。

## 16.8 GAIA 适配器

GAIA 入口是：

```text
evals/run_official.py
```

真实执行逻辑在：

```text
evals/official/gaia_adapter.py
```

运行命令：

```bat
python -m evals.run_official --benchmark gaia --gaia-split validation --limit 3
```

报告写入：

```text
evals/reports/official/<时间戳>-gaia.jsonl
```

GAIA 适配器做了四件事。

第一，固定加载数据：

```python
load_dataset("gaia-benchmark/GAIA", level_config, split=split)
rows = rows[offset:]
rows = rows[:limit]
```

因此同样的 split、level_config、offset、limit 会选中同一批题。

第二，给每道题创建独立 workspace：

```text
.mini-agent/gaia-workspaces/<task_id>
```

第三，处理附件：

```text
attachment_name：附件成功复制后的文件名
attachment_error：附件复制失败原因
```

如果题目元数据里有附件但复制失败，prompt 会明确告诉模型附件缺失，避免模型误以为文件存在。

第四，强制最终答案格式：

```text
Final answer: <短答案>
```

报告中会额外写入：

```text
prediction：模型完整回答
extracted_answer：从 Final answer 行抽取出的短答案
format_ok：是否成功抽取到 Final answer
exact_match：归一化后的 extracted_answer 是否等于标准答案
```

注意：当前 GAIA 报告没有保存完整 `agent.history()`，所以看不到完整工具调用轨迹。现在能对比的是 `prediction` 中呈现出来的解题过程，而不是每一步工具调用过程。

如果后续要做严格过程评估，应该在 `gaia_adapter.py` 中把下面内容也写进 jsonl：

```text
agent.history()
extract_tool_calls(agent.history())
duration_seconds
budget.to_dict()
```

这样 GAIA 才能像自建数据集一样比较工具调用、模型轮次、token 和成本。

## 16.9 报告对比脚本

当前新增了：

```text
evals/compare_reports.py
```

它读取两个自建评估报告的 `summary`，输出 Markdown 表格。

用法：

```bat
python -m evals.compare_reports evals\reports\20260821-110320-baseline-v2.json evals\reports\20260821-164054-optimized.json
```

它会对比：

```text
pass_rate
prompt_build_ms
schema_build_ms
prepare_total_ms
duration_seconds
input_tokens
output_tokens
cache_read_tokens
estimated_cost_usd
model_turns
tool_calls
```

## 16.10 自建数据集真实对比

本次 baseline-v2 报告：

```text
evals/reports/20260821-110320-baseline-v2.json
```

本次 optimized 报告：

```text
evals/reports/20260821-164054-optimized.json
```

整体指标：

| 指标 | baseline-v2 | optimized | 变化 |
|---|---:|---:|---:|
| 总题数 | 20 | 20 | - |
| 通过数 | 16 | 17 | +1 |
| 通过率 | 80.0% | 85.0% | +5.0 pct |
| 总耗时 | 534.29s | 249.68s | -53.3% |
| 预估成本 | $0.2816 | $0.2586 | -8.2% |
| 输入 tokens | 222081 | 224363 | +1.0% |
| 输出 tokens | 10078 | 6937 | -31.2% |
| model turns | 84 | 83 | -1 |
| tool calls | 74 | 73 | -1 |
| prompt_build_ms | 119.04ms | 67.70ms | -43.1% |

分类结果：

| 类别 | baseline-v2 | optimized | 变化 |
|---|---:|---:|---:|
| tool_calling | 8/10 | 8/10 | 不变 |
| coding | 8/10 | 9/10 | +1 |

逐题变化：

| case_id | 类别 | baseline-v2 | optimized | 说明 |
|---|---|---|---|---|
| code-003 | coding | 失败 | 通过 | 文件创建闭环变好 |
| code-008 | coding | 通过 | 失败 | README 修改任务出现回退 |
| code-009 | coding | 失败 | 通过 | Coding 修复任务变好 |

仍然失败的题：

```text
tool-008
tool-010
code-008
```

结论：

```text
optimized 的通过率从 80% 提升到 85%。
主要提升来自 coding 集，从 8/10 到 9/10。
耗时下降非常明显，总耗时减少约 53.3%。
输出 tokens 减少约 31.2%，成本下降约 8.2%。
但 tool_calling 没有提升，code-008 出现回退，需要继续看具体轨迹。
```

## 16.11 GAIA 结果对比

本次 baseline GAIA 报告：

```text
evals/reports/official/20260821-110344-gaia.jsonl
```

本次 optimized GAIA 报告：

```text
evals/reports/official/20260821-164716-gaia.jsonl
```

两次使用的是同一批 3 道题，task_id 完全一致。

整体结果：

| 指标 | baseline GAIA | optimized GAIA | 变化 |
|---|---:|---:|---:|
| 题数 | 3 | 3 | - |
| 通过数 | 2 | 2 | 不变 |
| 准确率 | 66.7% | 66.7% | 不变 |

逐题结果：

| task_id | 标准答案 | baseline | optimized | 说明 |
|---|---:|---|---|---|
| e1fc63a2... | 17 | 错 | 错 | 两边 `prediction` 都为空，`format_ok=false` |
| 8e867cd7... | 3 | 对 | 对 | 两边都抽取出 `3` |
| ec09fa32... | 3 | 对 | 对 | 两边都抽取出 `3` |

从结果看，GAIA 的准确率没有提升。最关键的问题是第一题：两边都没有产出回答，因此不是“答案抽取规则不够好”，而是 Agent 在这一题上没有完成有效解题。

## 16.12 GAIA 过程对比

GAIA 不能只看 `exact_match`。即使两边都答对，也要看过程是否更可靠、更短、更符合题目要求。

当前报告只保存了 `prediction`，没有保存完整工具调用轨迹，所以这里对比的是模型最终回答中呈现出的解题过程。

### 16.12.1 e1fc63a2：Kipchoge 配速与地月距离

题目要求：

```text
使用 Moon Wikipedia 页面上的最小 perigee 值，
按 Eliud Kipchoge 马拉松纪录配速计算到月球最近距离需要多少 thousand hours，
四舍五入到最近 1000 hours。
标准答案：17
```

baseline-v2：

```text
prediction 为空
extracted_answer 为空
format_ok = false
exact_match = false
```

optimized：

```text
prediction 为空
extracted_answer 为空
format_ok = false
exact_match = false
```

过程判断：

```text
两边都没有留下可分析的解题过程。
这说明当前失败点不在 Final answer 抽取，而在 Agent 没有完成回答。
后续要排查模型调用、工具调用、异常处理或预算停止原因。
```

### 16.12.2 8e867cd7：Mercedes Sosa 专辑数

题目要求统计 2000 到 2009 年间 Mercedes Sosa 发布的 studio albums 数量，标准答案是 `3`。

baseline-v2 过程：

```text
列出了 2005 的 Corazon Libre，
以及 2009 的 Cantora 1 / Cantora 2。
同时提到 Kiddle mirrors Wikipedia，
并额外解释 live albums 不算 studio albums。
最终输出 Final answer: 3。
```

optimized 过程：

```text
明确说已经拿到 Wikipedia 的 Studio Albums 表。
逐项筛选 1999、2005、2009、2009、2011。
排除 1999 和 2011，只保留 2005 与两个 2009。
最终输出 Final answer: 3。
```

过程对比：

```text
结论：optimized 明显更好，但不是“更聪明”，而是“更规范”
两边都答对，也都能抽取出最终答案。
optimized 的过程更贴近题目指定来源，筛选逻辑更清楚。
baseline-v2 也正确，但引用了 Kiddle 这种间接来源表述，过程可信度略弱。
```

这一题 optimized 的改进不体现在准确率，而体现在解题叙述更直接。

### 16.12.3 ec09fa32：Pick That Ping-Pong

题目要求从 1 到 100 号球中选择中奖概率最高的球，标准答案是 `3`。

baseline-v2 过程：

```text
先做 Markov Chain 分析。
给出三个初始位置的胜率：
position 1 = 1/3
position 2 = 5/9
position 3 = 17/27
然后额外给出 10000 次 simulation confirmation。
最终输出 Final answer: 3。
```

optimized 过程：

```text
直接按规则推导三个位置的胜率：
position 1 = 1/3
position 2 = 5/9
position 3 = 17/27
再解释 Ball 3 初始就在 position 3，
且比后续 ramp balls 更早获得完整机会。
最终输出 Final answer: 3。
```

过程对比：

```text
两边都答对，也都能抽取出最终答案。
baseline-v2 的过程更短，并补了模拟验证。
optimized 的推导更展开，但 prediction 字符数更长。
这一题不能说 optimized 更省输出，反而更啰嗦。
```

所以 GAIA 的过程结论要更谨慎：

```text
Mercedes Sosa：optimized 过程更好。
Ping-Pong：两边都正确；optimized 更展开，baseline-v2 更短且有模拟验证。
Kipchoge：两边都失败，没有有效过程。
```

## 16.13 本章结论

这轮优化在自建数据集上是有效的：

```text
通过率：80.0% -> 85.0%
总耗时：534.29s -> 249.68s
输出 tokens：10078 -> 6937
预估成本：$0.2816 -> $0.2586
```

但在 GAIA 上不能说整体变强：

```text
准确率：2/3 -> 2/3
第一题仍然空输出
正确题的过程质量有变化，但不是单向全面提升
```

因此本章最终判断是：

```text
自建 Coding Agent 任务：优化成立。
GAIA 泛化能力：结果未提升，只能看到局部过程改善。
下一轮重点应该是保存 GAIA 完整历史，并排查空输出题。
```

后续最值得补的不是继续改 prompt，而是先让 GAIA 报告记录完整过程：

```text
agent.history()
extract_tool_calls(agent.history())
duration_seconds
budget.to_dict()
stop_reason / exception
```

只有这样，GAIA 才能从“结果对比”升级成“过程对比”，也才能真正判断 Agent 是哪里变强、哪里退化。
