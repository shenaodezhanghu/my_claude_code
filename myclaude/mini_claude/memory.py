from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
from datetime import date
from typing import Any

from mini_claude.frontmatter import parse_frontmatter


MEMORY_TYPES = {"episodic", "semantic"}



@dataclass(frozen=True)
class MemoryEntry:
    name: str
    description: str
    memory_type: str
    tags: list[str]
    updated: str
    path: Path
    content: str


@dataclass(frozen=True)
class MemoryCandidate:
    content: str
    memory_type: str
    tags: list[str]
    importance: float
    reason: str



def load_memories(project_root: Path) -> list[MemoryEntry]:
    memory_dir = project_root / ".mini-memory"
    if not memory_dir.is_dir():
        return []

    entries: list[MemoryEntry] = []
    for path in sorted(memory_dir.glob("*.md")):
        if path.name.upper() == "MEMORY.MD":
            continue
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError:
            continue

        metadata, body = parse_frontmatter(raw)
        if not body:
            continue

        memory_type = metadata.get("type", "semantic")
        if memory_type not in MEMORY_TYPES:
            memory_type = "semantic"

        tags = [
            tag.strip()
            for tag in metadata.get("tags", "").split(",")
            if tag.strip()
        ]

        entries.append(
            MemoryEntry(
                name=metadata.get("name", path.stem),
                description=metadata.get(
                    "description",
                    body.splitlines()[0][:120],
                ),
                memory_type=memory_type,
                tags=tags,
                updated=metadata.get("updated", ""),
                path=path,
                content=body,
            )
        )
    return entries


def slugify_name(text: str) -> str:
    words = re.findall(r"[a-zA-Z0-9_\u4e00-\u9fff]+", text)
    name = "-".join(words[:6]).lower()
    return name or "memory"


def score_memory_candidate(
    content: str,
    tags: list[str],
) -> float:
    score = 0.0
    lowered = content.lower()

    if any(tag in tags for tag in ("user", "preference", "rule")):
        score += 0.35
    if any(tag in tags for tag in ("bug", "fix", "feedback")):
        score += 0.25
    if any(
        marker in lowered
        for marker in (
            "以后",
            "不要",
            "必须",
            "规则",
            "报错",
            "traceback",
            "error",
        )
    ):
        score += 0.25
    if len(content) >= 30:
        score += 0.15

    return min(score, 1.0)


def save_memory_entry(
    project_root: Path,
    candidate: MemoryCandidate,
) -> Path | None:
    if candidate.importance < 0.7:
        return None

    memory_dir = project_root / ".mini-memory"
    memory_dir.mkdir(parents=True, exist_ok=True)

    name = slugify_name(candidate.content)
    path = memory_dir / f"{name}.md"
    counter = 2
    while path.exists():
        path = memory_dir / f"{name}-{counter}.md"
        counter += 1

    path.write_text(
        (
            "---\n"
            f"name: {path.stem}\n"
            f"description: {candidate.reason}\n"
            f"type: {candidate.memory_type}\n"
            f"tags: {', '.join(candidate.tags)}\n"
            f"importance: {candidate.importance:.2f}\n"
            f"updated: {date.today().isoformat()}\n"
            "---\n\n"
            f"{candidate.content.strip()}\n"
        ),
        encoding="utf-8",
    )
    return path


def consolidate_memory_candidates(
    project_root: Path,
    candidates: list[MemoryCandidate],
) -> list[Path]:
    saved: list[Path] = []
    for candidate in candidates:
        path = save_memory_entry(project_root, candidate)
        if path is not None:
            saved.append(path)
    return saved


def extract_keywords(text: str) -> set[str]:
    lower = text.lower()
    english = {
        word
        for word in re.findall(r"[a-z0-9_]+", lower)
        if len(word) > 2
    }
    chinese_text = "".join(
        re.findall(r"[\u4e00-\u9fff]", lower)
    )
    chinese = {
        chinese_text[index : index + 2]
        for index in range(max(0, len(chinese_text) - 1))
    }
    return english | chinese


def format_memories(entries: list[MemoryEntry]) -> str:
    if not entries:
        return ""
    blocks = [
        (
            f"## {entry.name} ({entry.memory_type})\n"
            f"tags: {', '.join(entry.tags) or 'none'}\n"
            f"{entry.content}"
        )
        for entry in entries
    ]
    return (
        "# Memory\n"
        "以下内容是相关长期记忆，可能过时；涉及当前代码时必须重新核实。\n\n"
        + "\n\n".join(blocks)
    )


def keyword_recall(
    query: str,
    project_root: Path,
    limit: int = 3,
    memory_type: str = "any",
) -> str:
    query_words = extract_keywords(query)
    if not query_words:
        return ""

    scored: list[tuple[int, MemoryEntry]] = []
    for entry in load_memories(project_root):
        if memory_type != "any" and entry.memory_type != memory_type:
            continue
        searchable = (
            f"{entry.name} {entry.description} "
            f"{entry.memory_type} {' '.join(entry.tags)} "
            f"{entry.content}"
        )
        score = len(query_words & extract_keywords(searchable))
        if score > 0:
            scored.append((score, entry))

    selected = [
        entry
        for _, entry in sorted(
            scored,
            key=lambda item: item[0],
            reverse=True,
        )[:limit]
    ]
    return format_memories(selected)

