# 第十六章 Mini Claude 运行时优化

> 第十五章建立评估方法，回答“Agent 表现如何”。第十六章只做一件事：在不新增大型功能、不改变用户使用方式的前提下，优化 Mini Claude 的运行效率、输出质量和成本。
>
> 本章不再实现 Core daemon、TUI、IPC、前端或新的产品形态。那些属于架构扩展，不属于当前阶段的性能优化。

## 16.1 本章目标

前面章节已经对齐了原版完整 mini-claude 的主要能力：

- static/dynamic Prompt 与 `cache_control`；
- Deferred Tools 与 `tool_search`；
- 流式输出与只读工具并行；
- Context Compact 与大结果落盘；
- Memory、Plan Mode、Sub-Agent、MCP 和 Budget。

这些能力本身不能再写成“本项目优化点”，因为原版教程已经包含对应设计。本章关注的是当前 Python 实现中仍然存在的运行时浪费：

```text
每轮重复构造 Prompt
模型调用前准备工作串行等待
最终回答过长导致 output token 偏高
失败后容易重复犯错
没有用评估指标证明优化是否真的有效
```

本章所有优化都必须满足三个原则：

1. 不增加前端、daemon、TUI 这类新功能。
2. 不依赖额外模型调用作为常态流程。
3. 必须能在第十五章评估中做改进前后对比。

## 16.2 优化一：Prompt 构建分层缓存

### 16.2.1 要解决的问题

当前 `build_prompt_parts()` 每次模型调用前都会重新准备：

```text
static prompt
操作系统与当前目录
Git 信息
AGENTS.md / CLAUDE.md 项目说明
Plan Mode 提示
Memory 提示
Deferred Tool 名称
```

功能上没有问题，但其中很多内容并不是每轮都会变化。每轮全量重算会带来两个问题：

- Python 端模型调用前等待时间变长；
- dynamic prompt 更容易出现不必要变化，降低缓存稳定性。

### 16.2.2 优化思路

把 Prompt 拆成三层：

```text
Static Layer
  长期不变：角色、工作规则、安全规则

Session Layer
  会话内低频变化：项目说明、workspace、工具摘要

Turn Layer
  每轮变化：Plan 状态、必要 Git 状态、working memory、用户任务相关提醒
```

对应策略：

| 内容 | 刷新时机 |
|---|---|
| static prompt | 程序启动后复用 |
| AGENTS.md / CLAUDE.md | 路径或 mtime 变化时刷新 |
| Git 信息 | TTL 过期或文件写入后刷新 |
| deferred tool 摘要 | 工具激活状态变化时刷新 |
| Plan / working memory | 每轮按需刷新 |

### 16.2.3 推荐实现顺序

第一步，新建一个轻量缓存对象，例如 `PromptBuildCache`：

```python
from __future__ import annotations

from dataclasses import dataclass, field
import time


@dataclass
class CachedText:
    value: str = ""
    mtime: float | None = None
    updated_at: float = 0.0


@dataclass
class PromptBuildCache:
    static_prompt: str = ""
    project_instruction: CachedText = field(default_factory=CachedText)
    git_context: CachedText = field(default_factory=CachedText)
    deferred_tools_text: CachedText = field(default_factory=CachedText)
    git_ttl_seconds: float = 5.0

    def git_expired(self) -> bool:
        return time.monotonic() - self.git_context.updated_at > self.git_ttl_seconds
```

第二步，把 `find_project_instruction()` 和读取文件内容分开：

```text
find_project_instruction() 只负责找路径
read_project_instruction_cached() 根据 mtime 决定是否重读
```

第三步，Git 信息不要每轮无条件执行。至少做到：

```text
TTL 内复用
执行 write_file / edit_file / run_shell 后标记 dirty
dirty 时下一轮刷新
```

第四步，`ToolRegistry` 在工具激活状态变化时暴露一个版本号：

```text
register / activate / restore_activated
→ schema_version += 1
```

Prompt 构建缓存根据版本号判断 deferred tool 摘要是否需要重建。

### 16.2.4 第十五章如何评估

评估指标：

```text
prompt_build_ms
git_context_ms
instruction_read_count
system_message_rebuild_count
cached_tokens
first_model_request_delay_ms
```

对比方式：

```text
baseline：每轮直接 build_prompt_parts()
optimized：开启 PromptBuildCache
```

通过标准：

```text
任务成功率不下降
prompt_build_ms 下降
first_model_request_delay_ms 下降
cached_tokens 不下降
```

## 16.3 优化二：模型调用前准备阶段并行

### 16.3.1 要解决的问题

第十三章已经实现了“工具执行并行”：模型生成多个只读工具调用后，调度器可以并行执行。

本节优化的是另一段时间：模型请求发出之前的准备阶段。

模型调用前可能需要准备：

```text
读取项目说明
获取 Git 信息
准备工具 Schema
加载 Runtime State
恢复 Budget / Plan / activated tools
尝试连接 MCP Server
构造 PromptParts
```

这些任务并不全部互相依赖。如果串行执行，慢的部分会拖住整个首轮响应。

### 16.3.2 优化思路

把准备阶段拆成多个小任务：

```text
必须等待：
  workspace
  runtime state
  prompt parts
  active tool schema

可以后台预热：
  MCP 连接
  Git 信息刷新
  大型项目说明读取
```

MCP 可以放入准备阶段并行，但不应该阻塞普通任务：

```text
用户明确要用 MCP：等待 MCP 连接结果
普通本地任务：MCP 后台连接，不阻塞模型调用
MCP 失败：记录失败原因，本地工具继续可用
```

### 16.3.3 推荐实现顺序

第一步，先加性能计时，不急着并行：

```python
from contextlib import contextmanager
import time


@contextmanager
def measure(stats: dict[str, float], name: str):
    start = time.perf_counter()
    try:
        yield
    finally:
        stats[name] = stats.get(name, 0.0) + (
            time.perf_counter() - start
        ) * 1000
```

第二步，记录每轮准备耗时：

```text
prepare_total_ms
prompt_build_ms
git_context_ms
schema_build_ms
mcp_connect_ms
```

第三步，只把互不依赖且安全的准备任务并行化。第一版可以并行：

```text
Git 信息刷新
项目说明读取
工具 Schema 构造
MCP 后台连接
```

第四步，设置超时策略：

```text
本地准备任务超时：使用旧缓存或降级文本
MCP 连接超时：本轮不阻塞，下一轮继续尝试
```

### 16.3.4 第十五章如何评估

评估指标：

```text
prepare_total_ms
first_model_request_delay_ms
mcp_connect_ms
非 MCP 任务首轮耗时
MCP 任务首次可用时间
```

对比方式：

```text
baseline：准备阶段串行
optimized：准备阶段并行 + MCP 后台预热
```

通过标准：

```text
非 MCP 任务不再被 MCP 连接拖慢
first_model_request_delay_ms 下降
任务成功率不下降
MCP 失败不影响本地工具
```

## 16.4 优化三：最终回答长度策略

### 16.4.1 要解决的问题

流式输出只能让用户更早看到内容，不能减少模型真正生成的 token。如果最终回答很长，完整输出时间和输出费用仍然会上升。

当前 Prompt 只写了“回答简洁明确”，约束不够具体。模型在代码任务结束后可能输出：

```text
重复总结
过长解释
无关背景
已经在工具轨迹里出现过的细节
```

### 16.4.2 优化思路

给最终回答建立任务类型契约：

```text
代码修改任务：
  做了什么
  验证情况
  风险或未完成事项

报错诊断：
  原因
  修改位置
  验证方法

概念解释：
  先给结论
  再给必要原因

教程/文档任务：
  只有用户明确要求详细教程时才展开
```

注意：这不是修改流式输出延迟，而是减少模型生成内容本身。

### 16.4.3 推荐实现方式

在 `STATIC_PROMPT` 的沟通规则中增加：

```text
- 默认最终回答控制在 6 行以内。
- 代码修改任务只说明：做了什么、验证情况、风险或未完成事项。
- 没有运行验证命令时，不要说“测试通过”或“已验证”；应说明“未运行验证”。
- 用户要求教程、文档、详细解释时，才展开完整说明。
```

这个优化不需要额外模型调用，也不需要新增工具。

### 16.4.4 第十五章如何评估

评估指标：

```text
平均 output_tokens
最终回答字符数
完整输出耗时
最终回答信息缺失次数
用户追问次数
```

对比方式：

```text
baseline：原始沟通规则
optimized：加入最终回答长度策略
```

通过标准：

```text
output_tokens 下降
完整输出耗时下降
任务成功率和可理解性不下降
```

## 16.5 优化四：低成本自检

### 16.5.1 要解决的问题

模型可能在最终回答中说出工具轨迹不支持的话，例如：

```text
没有运行测试，却说测试通过
权限被拒绝，却说已经完成
文件不存在，却说删除成功
工具失败，却说验证完成
```

这类问题会降低输出质量。直接使用多答案投票式 Self-Consistency 成本太高，不适合当前项目。

### 16.5.2 优化思路

第一版只做低成本自检：通过 Prompt 约束最终声明，不额外调用模型。

核心规则：

```text
没有 run_shell 成功记录时，不要声称“测试通过”或“已验证”。
没有 write_file / edit_file / run_shell 成功记录时，不要声称已经修改、创建或删除文件。
工具失败或权限被拒绝时，最终回答必须说明限制，不能说任务已完成。
```

这不是把工具历史再塞一遍给模型，而是限制模型最终回答的声明边界。

### 16.5.3 后续工程化方向

等第十五章评估稳定后，可以再做程序级检查：

```text
记录本轮成功工具
扫描最终回答中的高风险声明
发现不支持的声明时，要求模型重写
```

但这会多一次模型调用，所以第一版不做。

### 16.5.4 第十五章如何评估

评估指标：

```text
不实完成声明次数
不实验证声明次数
工具失败后错误总结次数
额外模型调用次数
```

通过标准：

```text
不实声明下降
额外模型调用次数保持为 0
最终回答没有明显变啰嗦
```

## 16.6 优化五：失败时有限 Reflection

### 16.6.1 要解决的问题

当前项目有普通重试：

```text
网络失败 → with_retry
prompt too long → 压缩后再试一次
工具失败 → 错误结果返回给模型
```

但还没有 Reflection。模型拿到工具错误后，可能继续重复同样的错误参数，或者没有总结失败原因就进入下一步。

### 16.6.2 优化思路

Reflection 只在失败时触发，不能每轮触发。

触发条件：

```text
JSON tool arguments 解析失败
同一个工具连续失败
edit_file old_text not found
run_shell 非 0 退出
权限拒绝后继续尝试同类操作
prompt too long 压缩后仍失败
```

注入给模型的反思内容要短：

```text
上一步失败。请先判断：
1. 失败原因是什么？
2. 哪个假设错了？
3. 下一步最小修正动作是什么？
不要重复相同参数的失败工具调用。
```

每个用户任务最多触发 1 到 2 次。超过上限后直接把阻塞原因告诉用户，避免 Reflection 自己变成循环。

### 16.6.3 推荐实现方式

第一版不要新建复杂框架，只在 Agent 中维护本轮失败计数：

```text
failure_count
last_failed_tool_signature
reflection_count
```

当工具结果满足触发条件时，向 `self.messages` 追加一条短 user message，然后继续 Agent Loop。

不要额外开启单独评估器模型。

### 16.6.4 第十五章如何评估

评估指标：

```text
失败后恢复成功率
重复同参数失败次数
失败后平均额外轮数
Reflection 触发次数
失败任务总费用
```

通过标准：

```text
失败恢复率提升
重复失败下降
普通成功任务不触发 Reflection
Reflection 触发次数不超过上限
```

## 16.7 统一评估方式

第十六章的每个优化都必须回到第十五章评估体系中验证。不要一次打开全部优化，否则无法判断是哪一项带来变化。

推荐对比方式：

```text
baseline：第十三章完成后的标准 Mini Claude
optimized-1：只开启 Prompt 构建分层缓存
optimized-2：只开启准备阶段并行
optimized-3：只开启最终回答长度策略
optimized-4：只开启低成本自检
optimized-5：只开启失败时有限 Reflection
```

每组至少记录：

```text
任务成功率
平均模型调用轮数
平均工具调用次数
平均 input_tokens
平均 output_tokens
cache_read_tokens
总费用
prepare_total_ms
first_model_request_delay_ms
完整任务耗时
失败类型分布
```

保留优化的标准：

```text
成功率不下降
至少一个核心指标明显变好
没有引入新的高频失败类型
实现复杂度与收益匹配
```

## 16.8 本章完成标准

完成本章后，应该得到：

- 一组明确的运行时优化方案；
- 每个优化都知道解决什么问题；
- 每个优化都知道如何实现第一版；
- 每个优化都能回到第十五章做 A/B 对比；
- 不再把原版已有能力当作自己的优化点。

本章不是为了让项目变“大”，而是让已经完成的 Mini Claude 变得更快、更稳、更省。
