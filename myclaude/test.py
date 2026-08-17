from pathlib import Path
from tempfile import TemporaryDirectory

from mini_claude.memory import load_memories
from mini_claude.permissions import check_permission
from mini_claude.session_workspace import create_session_workspace
from mini_claude.tools import create_default_registry
from mini_claude.tools.base import ToolContext
from mini_claude.working_memory import load_working_memory


with TemporaryDirectory() as directory:
    root = Path(directory)
    workspace = create_session_workspace(root, "test-session")
    context = ToolContext(
        project_root=root,
        session_workspace=workspace,
    )
    registry = create_default_registry()

    for name in (
        "working_memory_read",
        "working_memory_update",
        "memory_search",
        "memory_add",
        "memory_forget",
    ):
        assert registry.get(name) is not None

    update_result = registry.execute(
        "working_memory_update",
        {
            "action": "set_goal",
            "value": "补全 Memory 生命周期",
        },
        context,
    )
    assert "补全 Memory 生命周期" in update_result

    candidate_result = registry.execute(
        "working_memory_update",
        {
            "action": "add_candidate",
            "candidate": {
                "content": (
                    "以后所有教程必须给出完整实现流程，"
                    "不要只写接口说明或省略工具注册步骤。"
                ),
                "suggested_type": "semantic",
                "tags": ["user", "feedback", "docs"],
                "reason": "稳定的教程编写偏好",
            },
        },
        context,
    )
    assert "memory_candidates" in candidate_result
    assert len(
        load_working_memory(
            workspace.working_memory_file
        ).memory_candidates
    ) == 1

    assert check_permission(
        "working_memory_read",
        {},
    ).action == "allow"
    assert check_permission(
        "memory_add",
        {"candidate_index": 0},
    ).action == "confirm"

    add_result = registry.execute(
        "memory_add",
        {"candidate_index": 0},
        context,
    )
    assert "Memory saved" in add_result
    assert len(load_memories(root)) == 1
    assert not load_working_memory(
        workspace.working_memory_file
    ).memory_candidates

    search_result = registry.execute(
        "memory_search",
        {"query": "教程完整实现", "memory_type": "semantic"},
        context,
    )
    assert "不要只写接口说明" in search_result

    memory_name = load_memories(root)[0].name
    assert check_permission(
        "memory_forget",
        {"name": memory_name},
    ).action == "confirm"

    forget_result = registry.execute(
        "memory_forget",
        {"name": memory_name},
        context,
    )
    assert "Memory forgotten" in forget_result
    assert load_memories(root) == []
    assert any(
        (root / ".mini-memory" / ".forgotten").glob("*.md")
    )

    print("Memory 验证通过")