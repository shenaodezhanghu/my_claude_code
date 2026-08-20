from __future__ import annotations

import json

from evals.evaluators.tool_calling import (
    ToolEvent,
    extract_tool_calls,
    extract_tool_trace,
    score_deferred_activation,
    score_duplicate_reads,
    score_expected_calls,
    score_parallel_groups,
    score_read_before_edit,
    score_required_order,
    score_tool_names,
    score_tool_trace,
)


def assistant_call(
    call_id: str,
    name: str,
    arguments: dict,
) -> dict:
    return {
        "id": call_id,
        "type": "function",
        "function": {
            "name": name,
            "arguments": json.dumps(arguments, ensure_ascii=False),
        },
    }


def successful_event(
    order: int,
    batch: int,
    name: str,
    arguments: dict,
    result: str = "ok",
) -> ToolEvent:
    return ToolEvent(
        order=order,
        batch=batch,
        call_id=f"call-{order}",
        name=name,
        arguments=arguments,
        result=result,
    )


def test_extract_trace_pairs_results_and_preserves_batch() -> None:
    messages = [
        {
            "role": "assistant",
            "tool_calls": [
                assistant_call("a", "read_file", {"path": "a.py"}),
                assistant_call("b", "read_file", {"path": "b.py"}),
            ],
        },
        {"role": "tool", "tool_call_id": "a", "content": "a content"},
        {"role": "tool", "tool_call_id": "b", "content": "b content"},
    ]

    events = extract_tool_trace(messages)

    assert [event.batch for event in events] == [0, 0]
    assert [event.result for event in events] == ["a content", "b content"]
    assert all(event.succeeded for event in events)
    assert extract_tool_calls(messages)[0]["arguments"] == {"path": "a.py"}


def test_extract_trace_keeps_invalid_json_observable() -> None:
    messages = [
        {
            "role": "assistant",
            "tool_calls": [
                {
                    "id": "bad",
                    "function": {
                        "name": "read_file",
                        "arguments": "{bad json",
                    },
                }
            ],
        }
    ]

    event = extract_tool_trace(messages)[0]

    assert event.arguments == {"__invalid_json__": "{bad json"}
    assert not event.succeeded


def test_tool_name_rules_detect_missing_forbidden_and_excess() -> None:
    events = [
        successful_event(0, 0, "read_file", {"path": "a.py"}),
        successful_event(1, 1, "write_file", {"path": "a.py"}),
    ]

    errors = score_tool_names(
        events,
        expected=["grep_search"],
        forbidden=["write_file"],
        max_calls=1,
    )

    assert "缺少必要工具：grep_search" in errors
    assert "调用了禁止工具：write_file" in errors
    assert "工具调用过多：2 > 1" in errors


def test_expected_call_matches_argument_subset_and_normalized_path() -> None:
    events = [
        successful_event(
            0,
            0,
            "read_file",
            {"path": ".\\Mini_Claude\\Agent.py", "optional": True},
        )
    ]

    errors = score_expected_calls(
        events,
        [
            {
                "name": "read_file",
                "arguments": {"path": "mini_claude/agent.py"},
            }
        ],
    )

    assert errors == []


def test_expected_call_rejects_wrong_argument_value() -> None:
    events = [
        successful_event(0, 0, "read_file", {"path": "README.md"})
    ]

    errors = score_expected_calls(
        events,
        [{"name": "read_file", "arguments": {"path": "AGENTS.md"}}],
    )

    assert errors == [
        "没有找到参数匹配的调用：read_file {'path': 'AGENTS.md'}"
    ]


def test_required_order_accepts_read_edit_test_sequence() -> None:
    events = [
        successful_event(0, 0, "read_file", {"path": "a.py"}),
        successful_event(1, 1, "edit_file", {"path": "a.py"}),
        successful_event(2, 2, "run_shell", {"command": "pytest"}),
    ]

    assert score_required_order(
        events,
        [["read_file", "edit_file"], ["edit_file", "run_shell"]],
    ) == []


def test_required_order_rejects_edit_before_read() -> None:
    events = [
        successful_event(0, 0, "edit_file", {"path": "a.py"}),
        successful_event(1, 1, "read_file", {"path": "a.py"}),
    ]

    assert score_required_order(
        events,
        [["read_file", "edit_file"]],
    ) == ["工具顺序错误：read_file 应早于 edit_file"]


def test_parallel_group_requires_same_batch_and_safe_tools() -> None:
    same_batch = [
        successful_event(0, 0, "read_file", {"path": "a.py"}),
        successful_event(1, 0, "read_file", {"path": "b.py"}),
    ]
    split_batch = [
        successful_event(0, 0, "read_file", {"path": "a.py"}),
        successful_event(1, 1, "read_file", {"path": "b.py"}),
    ]

    assert score_parallel_groups(
        same_batch,
        [["read_file", "read_file"]],
        {"read_file"},
    ) == []
    assert score_parallel_groups(
        split_batch,
        [["read_file", "read_file"]],
        {"read_file"},
    ) == ["没有在同一批次调用：['read_file', 'read_file']"]
    assert score_parallel_groups(
        same_batch,
        [["read_file", "read_file"]],
        set(),
    ) == ["并行组包含非并发安全工具：read_file"]


def test_duplicate_read_only_rejects_same_file_version() -> None:
    repeated = [
        successful_event(0, 0, "read_file", {"path": "a.py"}),
        successful_event(1, 1, "read_file", {"path": "./a.py"}),
    ]
    read_after_edit = [
        successful_event(0, 0, "read_file", {"path": "a.py"}),
        successful_event(1, 1, "edit_file", {"path": "a.py"}),
        successful_event(2, 2, "read_file", {"path": "a.py"}),
    ]

    assert score_duplicate_reads(repeated) == [
        "重复读取未变化文件：a.py"
    ]
    assert score_duplicate_reads(read_after_edit) == []


def test_read_before_edit_requires_successful_read_of_same_path() -> None:
    valid = [
        successful_event(0, 0, "read_file", {"path": "a.py"}),
        successful_event(1, 1, "edit_file", {"path": "./a.py"}),
    ]
    failed_read = [
        ToolEvent(
            order=0,
            batch=0,
            call_id="read",
            name="read_file",
            arguments={"path": "a.py"},
            result="Error: 文件不存在：a.py",
        ),
        successful_event(1, 1, "edit_file", {"path": "a.py"}),
    ]

    assert score_read_before_edit(valid) == []
    assert score_read_before_edit(failed_read) == [
        "edit_file 前没有成功读取同一文件：a.py"
    ]


def test_deferred_tool_requires_successful_search_in_earlier_batch() -> None:
    search_result = (
        '已激活以下工具：\n[{"name":"web_search",'
        '"description":"search"}]'
    )
    valid = [
        successful_event(
            0,
            0,
            "tool_search",
            {"query": "web search"},
            search_result,
        ),
        successful_event(1, 1, "web_search", {"query": "Python"}),
    ]
    same_batch = [
        successful_event(
            0,
            0,
            "tool_search",
            {"query": "web search"},
            search_result,
        ),
        successful_event(1, 0, "web_search", {"query": "Python"}),
    ]
    no_search = [
        successful_event(0, 0, "web_search", {"query": "Python"})
    ]

    assert score_deferred_activation(valid, {"web_search"}) == []
    assert score_deferred_activation(same_batch, {"web_search"}) == [
        "Deferred Tool 必须在 tool_search 的下一批调用：web_search"
    ]
    assert score_deferred_activation(no_search, {"web_search"}) == [
        "Deferred Tool 未经 tool_search 激活：web_search"
    ]


def test_unified_score_accepts_valid_trace() -> None:
    events = [
        successful_event(0, 0, "read_file", {"path": "a.py"}),
        successful_event(1, 0, "read_file", {"path": "b.py"}),
        successful_event(2, 1, "edit_file", {"path": "a.py"}),
        successful_event(3, 2, "run_shell", {"command": "pytest"}),
    ]

    errors = score_tool_trace(
        events,
        expected_tools=["read_file", "edit_file", "run_shell"],
        forbidden_tools=["web_search"],
        expected_calls=[
            {"name": "edit_file", "arguments": {"path": "a.py"}}
        ],
        required_order=[
            ["read_file", "edit_file"],
            ["edit_file", "run_shell"],
        ],
        expected_parallel_groups=[["read_file", "read_file"]],
        max_calls=4,
        reject_duplicate_reads=True,
        concurrency_safe_tools={"read_file"},
        deferred_tools={"web_search"},
    )

    assert errors == []
