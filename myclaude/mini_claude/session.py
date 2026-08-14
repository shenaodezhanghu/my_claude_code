import json
from pathlib import Path


# session_id = uuid4().hex
SESSION_DIR = Path.home() / ".mini-agent"
# SESSION_FILE = SESSION_DIR / f"{session_id}.json"


def get_session_file(session_id: str) -> Path:
    return SESSION_DIR / f"{session_id}.json"


def save_session(session_id: str, messages: list[dict]) -> None:
    SESSION_DIR.mkdir(parents=True, exist_ok=True)
    session_file = get_session_file(session_id)
    session_file.write_text(
        json.dumps(messages, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def load_session(session_id: str) -> list[dict]:
    session_file = get_session_file(session_id)

    if not session_file.is_file():
        return []

    try:
        value = json.loads(session_file.read_text(encoding="utf-8"))
        return value if isinstance(value, list) else []
    except (OSError, json.JSONDecodeError):
        return []