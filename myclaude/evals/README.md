# Mini Claude Evals

这个目录用于评估 Mini Claude 的工具调用、代码修改和后续优化效果。

## 当前包含什么

- `datasets/tool_calling.jsonl`：10 条工具调用评估样例
- `datasets/coding_tasks.jsonl`：10 条代码修改评估样例
- `fixtures/`：每条样例运行前复制到临时目录的小项目
- `workspace.py`：复制 fixture、快照文件、运行验证命令
- `evaluators/tool_calling.py`：检查工具名、参数、顺序、并行批次、重复读取、read-before-edit、Deferred Tool 激活
- `evaluators/coding.py`：检查修改文件范围、验证命令、最终回答
- `run_eval.py`：真实调用 Mini Claude 的自定义评估入口
- `run_official.py`：官方 Benchmark 接入入口
- `official/gaia_adapter.py`：运行 GAIA 小样本

## 先跑框架测试

这一步不调用模型，不花 token。

```bat
python -m pytest evals -q -p no:cacheprovider
```

期望结果：

```text
20 passed
```

## 运行少量真实评估

这一步会调用模型，会消耗百炼额度。

```bat
python -m evals.run_eval --suite tool --limit 1 --profile baseline
```

运行代码任务：

```bat
python -m evals.run_eval --suite coding --limit 1 --profile baseline
```

运行全部 20 条自定义回归集：

```bat
python -m evals.run_eval --suite all --profile baseline
```

报告会写入：

```text
evals/reports/
```

## 后续和第 16 章优化对比

先跑一次 baseline：

```bat
python -m evals.run_eval --suite all --profile baseline
```

完成第 16 章优化后，再跑：

```bat
python -m evals.run_eval --suite all --profile optimized
```

对比重点：

- pass_rate 是否下降
- input_tokens 是否减少
- output_tokens 是否减少
- cache_read_tokens 是否增加
- estimated_cost_usd 是否降低
- duration_seconds 是否降低
- model_turns 和 tool_calls 是否减少

## 接入 GAIA

GAIA 用来评估完整 Agent 的综合能力。它可能需要 HuggingFace 授权。

先安装依赖：

```bat
pip install datasets huggingface_hub
```

然后运行 Level 1 小样本：

```bat
python -m evals.run_official --benchmark gaia --limit 3
```

这一步会真实调用 Mini Claude Agent，成本比自定义小样本更高。
