from __future__ import annotations

import json
from pathlib import Path


# Session Index 跟随 Mini Claude 程序目录保存，不再写入用户主目录。
APP_ROOT = Path(__file__).resolve().parent.parent
INDEX_DIR = APP_ROOT / ".mini-agent"
INDEX_FILE = INDEX_DIR / "session-index.json"


def load_session_index() -> dict[str, str]:
    if not INDEX_FILE.is_file():
        return {}
    try:
        value = json.loads(INDEX_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            f"Session Index 无法读取：{INDEX_FILE}: {exc}"
        ) from exc
    if not isinstance(value, dict):
        raise RuntimeError("Session Index 必须是 JSON 对象")
    return {
        str(key): str(path)
        for key, path in value.items()
    }


def register_session(session_id: str, workspace_root: Path) -> None:
    INDEX_DIR.mkdir(parents=True, exist_ok=True)
    value = load_session_index()
    value[session_id] = str(workspace_root.resolve())

    temporary = INDEX_FILE.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(INDEX_FILE)


def find_session_root(session_id: str) -> Path | None:
    raw = load_session_index().get(session_id)
    if not raw:
        return None
    root = Path(raw).resolve()
    return root if root.is_dir() else None


def list_session_entries() -> list[dict[str, str]]:
    return [
        {
            "session_id": session_id,
            "workspace_root": workspace_root,
        }
        for session_id, workspace_root
        in load_session_index().items()
    ]
