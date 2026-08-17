import json
from pathlib import Path

from mini_claude.session_workspace import SessionWorkspace


LEGACY_SESSION_DIR = Path.home() / ".mini-agent"


def _read_messages(path: Path) -> list[dict] | None:
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, list) else None


def save_session(
    workspace: SessionWorkspace,
    messages: list[dict],
) -> None:
    workspace.root.mkdir(parents=True, exist_ok=True)
    workspace.messages_file.write_text(
        json.dumps(messages, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def load_session(workspace: SessionWorkspace) -> list[dict]:
    current = _read_messages(workspace.messages_file)
    if current is not None:
        return current

    legacy = LEGACY_SESSION_DIR / f"{workspace.session_id}.json"
    return _read_messages(legacy) or []


class SessionStateError(RuntimeError):
    pass


def load_runtime_state(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise SessionStateError(
            f"无法读取运行状态：{path}: {exc}"
        ) from exc
    except json.JSONDecodeError as exc:
        raise SessionStateError(
            f"运行状态 JSON 已损坏：{path}: {exc}"
        ) from exc
    if not isinstance(value, dict):
        raise SessionStateError(
            f"运行状态必须是 JSON 对象：{path}"
        )
    return value


def save_runtime_state(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(path)

def migrate_runtime_state(value: dict) -> dict:
    version = int(value.get("version", 1))

    if version == 1:
        migrated = dict(value)
        migrated["version"] = 2
        migrated.setdefault("workspace", {})
        migrated.setdefault("activated_tools", [])
        migrated.setdefault("budget_limits", {})
        migrated.setdefault("budget_usage", {})
        migrated.setdefault("plan", {})
        migrated.setdefault("last_usage", {})
        return migrated

    if version == 2:
        return value

    raise SessionStateError(
        f"无法迁移 Session 状态版本：{version}"
    )