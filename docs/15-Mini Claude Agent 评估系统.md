# 第十五章 Mini Claude Agent 评估系统

本章不再继续写“自治与续跑”。从这一章开始，我们给 Mini Claude 增加一套真正可运行的评估系统，用它回答三个问题：

```text
Agent 有没有完成任务？
Agent 完成任务的过程是否合理？
Agent 的耗时、Token 和费用是否值得继续优化？
```

当前实现只保留两类评估：

```text
自建回归评估集
GAIA 小样本流程评估
```

不再接入 BFCL。BFCL 更偏向评估模型本身的函数调用能力，而不是 Mini Claude 这个 Coding Agent 的端到端能力。

## 15.1 本章最终目录

本章完成后，`myclaude/myclaude/evals/` 目录如下：

```text
evals/
├── __init__.py
├── README.md
├── schema.py
├── workspace.py
├── run_eval.py
├── run_official.py
├── test_run_eval.py
├── test_workspace.py
├── datasets/
│   ├── tool_calling.jsonl
│   └── coding_tasks.jsonl
├── evaluators/
│   ├── __init__.py
│   ├── coding.py
│   ├── tool_calling.py
│   └── test_tool_calling.py
├── fixtures/
│   ├── add_function/
│   ├── create_config/
│   ├── first_pending/
│   ├── fix_greeting/
│   ├── fix_multiply/
│   ├── fix_parser/
│   ├── list_docs/
│   ├── list_python/
│   ├── multi_file_summary/
│   ├── normalize_user/
│   ├── parallel_reads/
│   ├── read_json/
│   ├── read_project/
│   ├── search_logs/
│   ├── search_permission/
│   ├── summarize_notes/
│   ├── update_markdown/
│   └── update_readme/
└── official/
    ├── __init__.py
    ├── common.py
    └── gaia_adapter.py
```

`evals/reports/` 是运行后生成的结果目录，不作为源码提交。

## 15.2 评估系统分成两层

第一层是自建回归集。它专门评估 Mini Claude 自己的能力：

```text
工具选择是否正确
参数是否合理
是否遵守 read-before-edit
是否重复读取同一文件
是否修改了不该修改的文件
修改后是否运行测试
最终文件和测试是否通过
```

第二层是 GAIA 小样本。它用来做更接近真实 Agent 的综合题：

```text
纯推理题
带附件题
多步分析题
最终答案格式控制
```

GAIA 当前只做小样本流程评估，不作为正式排行榜成绩。

## 15.3 定义评估数据结构

文件位置：

```text
evals/schema.py
```

这里定义两个核心结构。

`EvalCase` 表示一条评估样例：

```python
@dataclass(frozen=True)
class EvalCase:
    case_id: str
    category: str
    prompt: str
    fixture: str | None = None
    expected_tools: list[str] = field(default_factory=list)
    forbidden_tools: list[str] = field(default_factory=list)
    expected_calls: list[dict[str, Any]] = field(default_factory=list)
    required_order: list[list[str]] = field(default_factory=list)
    expected_parallel_groups: list[list[str]] = field(default_factory=list)
    reject_duplicate_reads: bool = False
    expected_files: list[str] = field(default_factory=list)
    forbidden_files: list[str] = field(default_factory=list)
    verify_command: list[str] = field(default_factory=list)
    expected_answer: str | None = None
    max_tool_calls: int | None = None
    notes: str | None = None
```

`EvalResult` 表示运行后的结果：

```python
@dataclass
class EvalResult:
    case_id: str
    category: str
    profile: str
    passed: bool
    final_answer: str
    tool_calls: list[dict[str, Any]]
    changed_files: list[str]
    duration_seconds: float
    model_turns: int = 0
    total_tool_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    estimated_cost_usd: float = 0.0
    errors: list[str] = field(default_factory=list)
```

这里没有只记录“通过/失败”，而是把工具调用、文件变化、耗时、Token 和费用都保存下来。第 16 章优化时，才能对比优化前后的真实指标。

## 15.4 创建隔离工作区

文件位置：

```text
evals/workspace.py
```

每条评估样例都不能直接修改 `fixtures/` 原始文件。运行前先复制到临时目录：

```python
def prepare_workspace(
    fixtures_root: Path,
    fixture: str | None,
) -> tuple[Path, Path]:
    temp_root = Path(tempfile.mkdtemp(prefix="mini-eval-"))
    workspace = temp_root / "workspace"

    if fixture is None:
        workspace.mkdir()
    else:
        source = fixtures_root / fixture
        if not source.is_dir():
            raise FileNotFoundError(f"Fixture 不存在：{source}")
        shutil.copytree(source, workspace)

    return temp_root, workspace
```

评估前后用文件 hash 做快照：

```python
before = snapshot_files(workspace)
...
after = snapshot_files(workspace)
changed = changed_files(before, after)
```

这样可以判断：

```text
目标文件有没有变化
测试文件有没有被误改
有没有额外改动无关文件
```

验证命令使用 `run_verify()` 执行：

```python
passed, output = run_verify(case.verify_command, workspace)
```

其中 `__PYTHON__` 会自动替换成当前解释器，避免 conda 环境和系统 Python 混用：

```json
"verify_command": ["__PYTHON__", "-m", "pytest", "-q"]
```

## 15.5 工具调用评估器

文件位置：

```text
evals/evaluators/tool_calling.py
```

第一步，把 Agent 的历史消息转换成工具轨迹：

```python
events = extract_tool_trace(messages)
```

每个 `ToolEvent` 记录：

```text
order：第几次工具调用
batch：同一轮 assistant tool_calls 的批次
call_id：工具调用 ID
name：工具名
arguments：工具参数
result：工具返回结果
succeeded：是否成功
```

然后组合多种规则评分：

```python
errors = score_tool_trace(
    events,
    expected_tools=case.expected_tools,
    forbidden_tools=case.forbidden_tools,
    expected_calls=case.expected_calls,
    required_order=case.required_order,
    expected_parallel_groups=case.expected_parallel_groups,
    max_calls=case.max_tool_calls,
    reject_duplicate_reads=case.reject_duplicate_reads,
    concurrency_safe_tools=CONCURRENCY_SAFE_TOOLS,
    deferred_tools=DEFERRED_TOOLS,
)
```

当前已经实现的检查包括：

```text
工具名是否包含必要工具
是否调用了禁止工具
工具调用总数是否超限
参数 key 和 value 是否匹配
read_file 是否早于 edit_file
并行读取是否在同一批 tool_calls 中出现
同一文件未变化时是否重复读取
Deferred Tool 是否先经过 tool_search 激活
```

这里评估的是“过程质量”。任务即使最后成功，如果工具调用太乱，也会被记录为需要优化。

## 15.6 代码任务评估器

文件位置：

```text
evals/evaluators/coding.py
```

代码任务不只看工具轨迹，还要看文件和测试。

修改范围检查：

```python
errors.extend(
    score_changed_files(
        changed,
        case.expected_files,
        case.forbidden_files,
    )
)
```

验证命令检查：

```python
errors.extend(score_verify_result(verify_passed, verify_output))
```

最终回答检查：

```python
errors.extend(score_expected_answer(final_answer, case.expected_answer))
```

这三层分别回答：

```text
有没有改对文件
改完后测试有没有通过
最终回答有没有包含必要信息
```

## 15.7 自建工具调用数据集

文件位置：

```text
evals/datasets/tool_calling.jsonl
```

当前有 10 条工具调用样例，覆盖：

```text
读取 README
grep 搜索权限函数
同时读取两个文件
列出 Python 文件
读取 JSON
多文件摘要
搜索日志 ERROR
列出 docs 下 Markdown 文件
缺失文件友好提示
环境信息读取
```

示例：

```json
{
  "case_id": "tool-001",
  "category": "tool_calling",
  "prompt": "读取 README.md，告诉我这个项目的名字。",
  "fixture": "read_project",
  "expected_tools": ["read_file"],
  "forbidden_tools": ["write_file", "edit_file", "run_shell", "web_search"],
  "expected_calls": [
    {
      "name": "read_file",
      "arguments": {"path": "README.md"}
    }
  ],
  "expected_answer": "Mini Claude",
  "max_tool_calls": 2
}
```

工具调用数据集主要评估：

```text
该读文件时是否读文件
该搜索时是否用 grep_search
只读任务是否避免写入和 shell
参数是否接近任务要求
是否有不必要的探索
```

## 15.8 自建代码任务数据集

文件位置：

```text
evals/datasets/coding_tasks.jsonl
```

当前有 10 条代码任务，覆盖：

```text
新增函数
修复字符串格式
创建 JSON 配置
修改 Markdown
修复乘法
列表查找
异常处理
README 更新
字典字段归一化
读取两个文件后生成总结
```

示例：

```json
{
  "case_id": "code-001",
  "category": "coding",
  "prompt": "在 calculator.py 中增加 calculate_sum(a, b)，保留 subtract，并运行测试验证。",
  "fixture": "add_function",
  "expected_tools": ["read_file", "edit_file", "run_shell"],
  "forbidden_tools": ["web_search"],
  "expected_calls": [
    {"name": "read_file", "arguments": {"path": "calculator.py"}},
    {"name": "edit_file", "arguments": {"path": "calculator.py"}}
  ],
  "required_order": [
    ["read_file", "edit_file"],
    ["edit_file", "run_shell"]
  ],
  "expected_files": ["calculator.py"],
  "forbidden_files": ["test_calculator.py"],
  "verify_command": ["__PYTHON__", "-m", "pytest", "-q"],
  "max_tool_calls": 10
}
```

代码任务数据集主要评估：

```text
是否先读再改
是否只改目标文件
是否保留测试文件
是否运行验证命令
是否真正让测试通过
```

## 15.9 Fixture 的作用

文件位置：

```text
evals/fixtures/
```

每个 fixture 都是一个很小的项目。例如 `add_function/`：

```text
add_function/
├── calculator.py
└── test_calculator.py
```

初始的 `calculator.py` 故意没有 `calculate_sum()`：

```python
def subtract(a: int, b: int) -> int:
    return a - b
```

测试文件要求它存在：

```python
from calculator import calculate_sum, subtract


def test_calculate_sum() -> None:
    assert calculate_sum(2, 3) == 5
```

评估时复制这个 fixture 到临时目录，让 Agent 在临时目录中修改。这样可以反复跑评估，不会污染原始样例。

## 15.10 自建评估入口

文件位置：

```text
evals/run_eval.py
```

它负责完整串起流程：

```text
读取数据集
→ 复制 fixture 到临时 workspace
→ 创建 MINI_CLUE_AGENT
→ agent.chat(prompt)
→ 快照文件变化
→ 运行 verify_command
→ 工具轨迹评分
→ 文件与测试评分
→ 输出 JSON 报告
```

评估入口会自动加载项目根目录 `.env`：

```python
PROJECT_ROOT = EVALS_ROOT.parent
load_dotenv(PROJECT_ROOT / ".env")
```

因此直接运行即可：

```bat
cd E:\研究生\学习\ai_study\claude-code\claude-code-from-scratch\myclaude\myclaude
python -m evals.run_eval --suite all --limit 3 --profile baseline
```

参数说明：

```text
--suite tool      只跑工具调用集
--suite coding    只跑代码任务集
--suite all       两个都跑
--limit 3         只跑前 3 条，节省 Token
--profile baseline 标记本次报告名称
--repeat 3        每条重复跑 3 次，看稳定性
```

完整跑 20 条：

```bat
python -m evals.run_eval --suite all --profile baseline
```

报告生成到：

```text
evals/reports/
```

报告是运行产物，不提交 Git。

## 15.11 报告怎么看

报告的 `summary` 会记录整体指标：

```json
{
  "total": 20,
  "passed": 15,
  "pass_rate": 0.75,
  "duration_seconds": 281.67,
  "input_tokens": 244627,
  "output_tokens": 7986,
  "cache_read_tokens": 165719,
  "estimated_cost_usd": 0.2879,
  "model_turns": 80,
  "tool_calls": 75
}
```

每条结果会记录：

```text
case_id
category
passed
final_answer
tool_calls
changed_files
duration_seconds
model_turns
input_tokens / output_tokens
estimated_cost_usd
errors
```

分析失败时不要只看 `passed=false`，要继续区分：

```text
任务真的失败
任务完成但工具调用太多
任务完成但评估规则太死
缺少测试验证
附件或数据没有准备好
```

本次 baseline 暴露出的典型问题：

```text
工具调用可能过多
有时修改代码后没有运行测试
创建文件任务 code-003 真实失败
部分规则需要支持等价路径和等价 glob
```

## 15.12 GAIA 小样本评估

文件位置：

```text
evals/run_official.py
evals/official/gaia_adapter.py
evals/official/common.py
```

GAIA 用来测试更接近真实 Agent 的综合能力。运行前安装：

```bat
pip install datasets huggingface_hub
```

如果 HuggingFace 没有权限，先登录：

```bat
huggingface-cli login
```

GAIA 读取方式：

```python
from datasets import load_dataset

dataset = load_dataset(
    "gaia-benchmark/GAIA",
    "2023_level1",
    split="validation",
)
```

运行 3 条：

```bat
python -m evals.run_official --benchmark gaia --gaia-split validation --limit 3
```

只重跑第 3 条：

```bat
python -m evals.run_official --benchmark gaia --gaia-split validation --limit 1 --offset 2
```

参数含义：

```text
--offset 2  跳过前两题
--limit 1   只跑一题
```

GAIA 报告生成到：

```text
evals/reports/official/
```

同样属于运行产物，不提交 Git。

## 15.13 GAIA 当前评分边界

如果跑 `test` split，很多 `Final answer` 会是：

```text
?
```

这代表测试集隐藏答案，本地不能算正式分数。要本地评分，优先跑：

```bat
python -m evals.run_official --benchmark gaia --gaia-split validation --limit 3
```

当前 `exact_match` 只做最简单比较：

```text
prediction.strip().lower() == final_answer.strip().lower()
```

所以如果标准答案是 `3`，模型输出 `Ball 3` 或一大段解释，程序会判失败，但人工语义上可能是正确的。

后续应该优化为：

```text
Prompt 强制最后输出 Final answer: <短答案>
报告中增加 extracted_answer
评分时比较 extracted_answer 和 final_answer
附件题检查 file_path 是否成功放入 workspace
```

## 15.14 框架测试

先跑不调用模型的测试：

```bat
python -m pytest evals -q -p no:cacheprovider
```

当前期望：

```text
19 passed
```

这个测试只验证评估框架本身：

```text
数据集能被读取
fixture 都存在
workspace 能复制
verify_command 能执行
工具轨迹评分器正确
代码评分器正确
```

它不调用百炼，不消耗 Token。

## 15.15 本章完成标准

完成本章后，应该具备以下能力：

```text
能跑 20 条自建回归评估
能得到 pass_rate、duration、tokens、cost、turns、tool_calls
能定位每条失败原因
能区分任务失败和评估规则过严
能跑 GAIA validation/test 小样本
能用 --offset 重跑指定 GAIA 题目
能为第 16 章优化提供 baseline
```

第 16 章优化时，先跑：

```bat
python -m evals.run_eval --suite all --profile baseline
```

优化后再跑：

```bat
python -m evals.run_eval --suite all --profile optimized
```

对比重点：

```text
pass_rate 不能下降
tool_calls 是否减少
model_turns 是否减少
duration_seconds 是否降低
input_tokens 是否减少
estimated_cost_usd 是否降低
```
