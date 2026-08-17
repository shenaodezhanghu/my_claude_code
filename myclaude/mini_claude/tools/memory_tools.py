from __future__ import annotations

import json

from .base import Tool, ToolContext
from ..memory import (
    MemoryCandidate,
    consolidate_memory_candidates,
    forget_memory,
    keyword_recall,
    score_memory_candidate,
)
from ..working_memory import (
    WorkingMemoryCandidate,
    load_working_memory,
    save_working_memory,
)


WORKING_CANDIDATE_THRESHOLD = 0.4


class MemorySearchTool(Tool):
    read_only = True
    concurrency_safe = True

    def __init__(self) -> None:
        super().__init__(
            "memory_search",
            "查询长期记忆，支持 semantic 和 episodic。",
        )

    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "memory_type": {
                    "type": "string",
                    "enum": ["semantic", "episodic", "any"],
                    "default": "any",
                },
                "limit": {"type": "integer", "default": 3},
            },
            "required": ["query"],
            "additionalProperties": False,
        }

    def run(self, args: dict, context: ToolContext) -> str:
        query = str(args.get("query", ""))
        limit = max(1, min(int(args.get("limit", 3)), 10))
        memory_type = str(args.get("memory_type", "any"))
        if memory_type not in {"semantic", "episodic", "any"}:
            return "Error: invalid memory type."
        return keyword_recall(
            query,
            context.project_root,
            limit=limit,
            memory_type=memory_type,
        ) or "No related memory found."


class WorkingMemoryReadTool(Tool):
    read_only = True
    concurrency_safe = True

    def __init__(self) -> None:
        super().__init__(
            "working_memory_read",
            "读取当前会话的目标、计划、观察、待办、阻塞点和记忆候选。",
        )

    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        }

    def run(self, args: dict, context: ToolContext) -> str:
        workspace = context.session_workspace
        if workspace is None:
            return "Error: session workspace is not available."

        memory = load_working_memory(
            workspace.working_memory_file
        )
        return json.dumps(
            memory.to_dict(),
            ensure_ascii=False,
            indent=2,
        )


class WorkingMemoryUpdateTool(Tool):
    def __init__(self) -> None:
        super().__init__(
            "working_memory_update",
            "更新当前会话工作状态，或管理尚未固化的记忆候选。",
        )

    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": [
                        "set_goal",
                        "replace_plan",
                        "add_observation",
                        "add_todo",
                        "complete_todo",
                        "add_blocker",
                        "remove_blocker",
                        "add_candidate",
                        "update_candidate",
                        "remove_candidate",
                    ],
                },
                "value": {"type": "string"},
                "items": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "index": {"type": "integer", "minimum": 0},
                "candidate": {
                    "type": "object",
                    "properties": {
                        "content": {"type": "string"},
                        "suggested_type": {
                            "type": "string",
                            "enum": ["episodic", "semantic"],
                        },
                        "tags": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "reason": {"type": "string"},
                    },
                    "required": [
                        "content",
                        "suggested_type",
                        "tags",
                        "reason",
                    ],
                    "additionalProperties": False,
                },
            },
            "required": ["action"],
            "additionalProperties": False,
        }

    def run(self, args: dict, context: ToolContext) -> str:
        workspace = context.session_workspace
        if workspace is None:
            return "Error: session workspace is not available."

        memory = load_working_memory(
            workspace.working_memory_file
        )
        action = str(args.get("action", ""))

        if action == "set_goal":
            memory.goal = str(args.get("value", "")).strip()

        elif action == "replace_plan":
            items = args.get("items")
            if not isinstance(items, list):
                return "Error: replace_plan requires items."
            memory.plan = [str(item) for item in items]

        elif action in {
            "add_observation",
            "add_todo",
            "add_blocker",
        }:
            value = str(args.get("value", "")).strip()
            if not value:
                return f"Error: {action} requires value."
            target = {
                "add_observation": memory.observations,
                "add_todo": memory.todos,
                "add_blocker": memory.blockers,
            }[action]
            target.append(value)

        elif action in {"complete_todo", "remove_blocker"}:
            index = int(args.get("index", -1))
            target = (
                memory.todos
                if action == "complete_todo"
                else memory.blockers
            )
            if index < 0 or index >= len(target):
                return "Error: state item does not exist."
            target.pop(index)

        elif action in {"add_candidate", "update_candidate"}:
            value = args.get("candidate")
            if not isinstance(value, dict):
                return f"Error: {action} requires candidate."

            content = str(value.get("content", "")).strip()
            tags = [str(tag) for tag in value.get("tags", [])]
            suggested_type = str(
                value.get("suggested_type", "episodic")
            )
            reason = str(value.get("reason", "")).strip()
            if not content or not reason:
                return "Error: candidate content and reason are required."
            if suggested_type not in {"episodic", "semantic"}:
                return "Error: invalid suggested memory type."

            importance = score_memory_candidate(content, tags)
            if importance < WORKING_CANDIDATE_THRESHOLD:
                return (
                    "Memory candidate skipped: "
                    f"importance={importance:.2f}."
                )

            candidate = WorkingMemoryCandidate(
                content=content,
                suggested_type=suggested_type,
                tags=tags,
                importance=importance,
                reason=reason,
            )
            if action == "add_candidate":
                duplicate = any(
                    item.content == candidate.content
                    for item in memory.memory_candidates
                )
                if duplicate:
                    return "Memory candidate already exists."
                memory.memory_candidates.append(candidate)
            else:
                index = int(args.get("index", -1))
                if index < 0 or index >= len(
                        memory.memory_candidates
                ):
                    return "Error: memory candidate does not exist."
                memory.memory_candidates[index] = candidate

        elif action == "remove_candidate":
            index = int(args.get("index", -1))
            if index < 0 or index >= len(memory.memory_candidates):
                return "Error: memory candidate does not exist."
            memory.memory_candidates.pop(index)

        else:
            return f"Error: unsupported working memory action: {action}"

        save_working_memory(
            workspace.working_memory_file,
            memory,
        )
        return json.dumps(
            memory.to_dict(),
            ensure_ascii=False,
            indent=2,
        )


class MemoryAddTool(Tool):
    def __init__(self) -> None:
        super().__init__(
            "memory_add",
            "将当前 working memory 中的候选固化为长期记忆。",
        )

    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "candidate_index": {
                    "type": "integer",
                    "minimum": 0,
                    "description": "memory_candidates 中的候选下标",
                },
                "memory_type": {
                    "type": "string",
                    "enum": ["semantic", "episodic"],
                    "description": "可选；确认固化时修正候选类型",
                },
            },
            "required": ["candidate_index"],
            "additionalProperties": False,
        }

    def run(self, args: dict, context: ToolContext) -> str:
        workspace = context.session_workspace
        if workspace is None:
            return "Error: session workspace is not available."

        working = load_working_memory(
            workspace.working_memory_file
        )
        index = int(args["candidate_index"])
        if index < 0 or index >= len(working.memory_candidates):
            return "Error: memory candidate does not exist."

        draft = working.memory_candidates[index]
        memory_type = str(
            args.get("memory_type") or draft.suggested_type
        )
        if memory_type not in {"semantic", "episodic"}:
            return "Error: invalid memory type."
        candidate = MemoryCandidate(
            content=draft.content,
            memory_type=memory_type,
            tags=draft.tags,
            importance=draft.importance,
            reason=draft.reason,
        )
        saved = consolidate_memory_candidates(
            context.project_root,
            [candidate],
        )
        if not saved:
            return "Memory candidate skipped: importance too low."

        working.memory_candidates.pop(index)
        save_working_memory(
            workspace.working_memory_file,
            working,
        )
        return f"Memory saved: {saved[0].relative_to(context.project_root)}"


class MemoryForgetTool(Tool):
    def __init__(self) -> None:
        super().__init__(
            "memory_forget",
            "遗忘过时或错误的长期记忆；文件会移入可恢复归档目录。",
        )

    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "memory_search 返回的精确记忆名称",
                },
            },
            "required": ["name"],
            "additionalProperties": False,
        }

    def run(self, args: dict, context: ToolContext) -> str:
        name = str(args.get("name", "")).strip()
        if not name:
            return "Error: memory name is required."

        archived = forget_memory(context.project_root, name)
        if archived is None:
            return "Error: memory does not exist or name is not unique."
        return (
            "Memory forgotten and archived: "
            f"{archived.relative_to(context.project_root)}"
        )