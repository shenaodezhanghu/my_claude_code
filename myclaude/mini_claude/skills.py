from __future__ import annotations

import re
from pathlib import Path

SKILL_NAME_PATTERN = re.compile(r"^[a-zA-Z0-9_-]+$")

def resolve_skill(text: str, project_root: Path) -> str|None:
    if not text.startswith("/"):
        return None

    name, _, rest = text[1: ].partition(" ")
    if not SKILL_NAME_PATTERN.fullmatch(name):
        return None

    skill_file = project_root / ".mini-skills" / f"{name}.md"
    if not skill_file.is_file():
        return None

    try: prompt = skill_file.read_text(encoding="utf-8").strip()
    except OSError:
        return None

    if not prompt:
        return None

    arguments = rest.strip()
    return f"{prompt}\n\n{arguments}" if arguments else prompt
