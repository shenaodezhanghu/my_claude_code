from __future__ import annotations

import re
from operator import index
from pathlib import Path


def extract_keywords(text: str) -> set[str]:
    lower_text = text.lower()
    english = {word for word in re.findall(r"[a-z0-9_]+", lower_text) if len(word) > 2}

    chinese_char = re.findall(r"[\u4e00-\u9fff]", lower_text)
    chinese = {
        "".join(chinese_char[index: index + 2])
        for index in range(len(chinese_char) - 1)
    }

    return english | chinese


def recall_memories(query: str, project_root: Path) -> str:
    memory_dir = project_root / ".mini-memory"
    if not memory_dir.is_dir():
        return ""

    query_words = extract_keywords(query)
    if not query_words:
        return ""

    scored:list[tuple[int, str]] = []

    for path in memory_dir.glob("*.md"):
        try:
            text = path.read_text(encoding="utf-8").strip()
        except OSError:
            continue

        score = len(query_words & extract_keywords(text))
        if score > 0:
            scored.append((score, text))

    if not scored:
        return ""


    top_memories = sorted(scored, key=lambda item: item[0], reverse=True)[:3]

    content = "\n".join(f"-{text}\n" for _, text in top_memories)
    return (
    "\n\n# Memory\n"
    "以下是与当前问题相关的长期记忆。"
    "记忆可能已经过时，涉及当前代码状态时必须使用工具核实。\n"
    f"{content}"
    )