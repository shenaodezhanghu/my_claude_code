from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import time
from uuid import uuid4

from dotenv import load_dotenv

from evals.evaluators.coding import (
    score_changed_files,
    score_expected_answer,
    score_verify_result,
)
from evals.evaluators.tool_calling import (
    extract_tool_calls,
    extract_tool_trace,
    score_tool_trace,
)
from evals.schema import EvalCase, EvalResult
from evals.workspace import (
    changed_files,
    prepare_workspace,
    run_verify,
    snapshot_files,
)


EVALS_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = EVALS_ROOT.parent
load_dotenv(PROJECT_ROOT / ".env")
DATASETS = EVALS_ROOT / "datasets"
FIXTURES = EVALS_ROOT / "fixtures"
REPORTS = EVALS_ROOT / "reports"

CONCURRENCY_SAFE_TOOLS = {
    "read_file",
    "list_files",
    "grep_search",
    "web_fetch",
    "web_search",
    "environment_info",
    "memory_search",
    "working_memory_read",
}
DEFERRED_TOOLS = {
    "web_fetch",
    "web_search",
}


def load_cases(path: Path) -> list[EvalCase]:
    cases: list[EvalCase] = []
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(),
        1,
    ):
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}:{line_number} JSON 错误：{exc}") from exc
        cases.append(EvalCase(**raw))
    return cases


def dataset_paths(suite: str) -> list[Path]:
    mapping = {
        "tool": [DATASETS / "tool_calling.jsonl"],
        "coding": [DATASETS / "coding_tasks.jsonl"],
        "all": [
            DATASETS / "tool_calling.jsonl",
            DATASETS / "coding_tasks.jsonl",
        ],
    }
    return mapping[suite]


def select_cases(suite: str, limit: int | None) -> list[EvalCase]:
    cases: list[EvalCase] = []
    for path in dataset_paths(suite):
        cases.extend(load_cases(path))
    return cases[:limit] if limit is not None else cases


def score_case(
    case: EvalCase,
    messages: list[dict],
    changed: list[str],
    verify_passed: bool,
    verify_output: str,
    final_answer: str,
) -> list[str]:
    events = extract_tool_trace(messages)
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
    errors.extend(
        score_changed_files(
            changed,
            case.expected_files,
            case.forbidden_files,
        )
    )
    errors.extend(score_verify_result(verify_passed, verify_output))
    errors.extend(score_expected_answer(final_answer, case.expected_answer))
    return errors


def run_one_case(
    case: EvalCase,
    *,
    profile: str,
    model: str | None,
    repeat_index: int,
) -> EvalResult:
    from mini_claude.agent import MINI_CLUE_AGENT

    temp_root, workspace = prepare_workspace(FIXTURES, case.fixture)
    started = time.perf_counter()
    final_answer = ""
    errors: list[str] = []

    try:
        before = snapshot_files(workspace)
        agent = MINI_CLUE_AGENT(
            session_id=f"eval-{profile}-{case.case_id}-{repeat_index}-{uuid4().hex}",
            permission_mode="accept_edits",
            model=model,
            project_root=workspace,
            input_price_per_million=2,
            output_price_per_million=8,
            cache_read_price_per_million=0.4,
        )
        try:
            final_answer = agent.chat(case.prompt)
        finally:
            agent.close()

        after = snapshot_files(workspace)
        changed = changed_files(before, after)
        verify_passed, verify_output = run_verify(
            case.verify_command,
            workspace,
        )
        errors = score_case(
            case,
            agent.history(),
            changed,
            verify_passed,
            verify_output,
            final_answer,
        )
        budget = agent.budget.to_dict()
        tool_calls = extract_tool_calls(agent.history())
        return EvalResult(
            case_id=case.case_id,
            category=case.category,
            profile=profile,
            passed=not errors,
            final_answer=final_answer,
            tool_calls=tool_calls,
            changed_files=changed,
            duration_seconds=time.perf_counter() - started,
            model_turns=budget["turns"],
            total_tool_calls=len(tool_calls),
            input_tokens=budget["input_tokens"],
            output_tokens=budget["output_tokens"],
            cache_read_tokens=budget["cache_read_tokens"],
            cache_creation_tokens=budget["cache_creation_tokens"],
            estimated_cost_usd=budget["estimated_cost_usd"],
            prompt_build_ms=agent.last_timings.get(
                "prompt_build_ms",
                0.0,
            ),
            schema_build_ms=agent.last_timings.get(
                "schema_build_ms",
                0.0,
            ),
            prepare_total_ms=agent.last_timings.get(
                "prepare_total_ms",
                0.0,
            ),
            errors=errors,
        )
    except Exception as exc:
        return EvalResult(
            case_id=case.case_id,
            category=case.category,
            profile=profile,
            passed=False,
            final_answer=final_answer,
            tool_calls=[],
            changed_files=[],
            duration_seconds=time.perf_counter() - started,
            errors=[f"评估运行异常：{type(exc).__name__}: {exc}"],
        )
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def summarize(results: list[EvalResult]) -> dict:
    total = len(results)
    passed = sum(1 for item in results if item.passed)
    def average(values: list[float]) -> float:
        return sum(values) / len(values) if values else 0.0

    return {
        "total": total,
        "passed": passed,
        "pass_rate": passed / total if total else 0.0,
        "duration_seconds": sum(item.duration_seconds for item in results),
        "input_tokens": sum(item.input_tokens for item in results),
        "output_tokens": sum(item.output_tokens for item in results),
        "cache_read_tokens": sum(item.cache_read_tokens for item in results),
        "cache_creation_tokens": sum(
            item.cache_creation_tokens for item in results
        ),
        "estimated_cost_usd": sum(
            item.estimated_cost_usd for item in results
        ),
        "model_turns": sum(item.model_turns for item in results),
        "tool_calls": sum(item.total_tool_calls for item in results),
        "prompt_build_ms": average(
            [item.prompt_build_ms for item in results]
        ),
        "schema_build_ms": average(
            [item.schema_build_ms for item in results]
        ),
        "prepare_total_ms": average(
            [item.prepare_total_ms for item in results]
        ),
    }


def save_report(results: list[EvalResult], profile: str) -> Path:
    REPORTS.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    path = REPORTS / f"{stamp}-{profile}.json"
    payload = {
        "profile": profile,
        "summary": summarize(results),
        "results": [item.to_dict() for item in results],
    }
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path


def print_summary(results: list[EvalResult], report: Path) -> None:
    summary = summarize(results)
    print(
        "Eval Summary: "
        f"{summary['passed']}/{summary['total']} passed, "
        f"pass_rate={summary['pass_rate']:.2%}, "
        f"cost=${summary['estimated_cost_usd']:.6f}, "
        f"duration={summary['duration_seconds']:.2f}s"
    )
    for result in results:
        marker = "PASS" if result.passed else "FAIL"
        print(f"{marker} {result.case_id} ({result.category})")
        for error in result.errors:
            print(f"  - {error}")
    print(f"Report: {report}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Mini Claude eval runner")
    parser.add_argument(
        "--suite",
        choices=["tool", "coding", "all"],
        default="all",
    )
    parser.add_argument("--profile", default="baseline")
    parser.add_argument("--model", default=None)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--repeat", type=int, default=1)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    cases = select_cases(args.suite, args.limit)
    results: list[EvalResult] = []
    for repeat_index in range(args.repeat):
        for case in cases:
            print(f"Running {case.case_id} repeat={repeat_index + 1}")
            results.append(
                run_one_case(
                    case,
                    profile=args.profile,
                    model=args.model,
                    repeat_index=repeat_index,
                )
            )
    report = save_report(results, args.profile)
    print_summary(results, report)


if __name__ == "__main__":
    main()
