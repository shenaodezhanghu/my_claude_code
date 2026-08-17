from collections.abc import Callable
from dataclasses import dataclass
import shlex


CommandHandler = Callable[[list[str]], str | None]


def unquote(value: str) -> str:
    if (
        len(value) >= 2
        and value[0] == value[-1]
        and value[0] in {'"', "'"}
    ):
        return value[1:-1]
    return value


@dataclass(frozen=True)
class CommandSpec:
    name: str
    usage: str
    description: str
    handler: CommandHandler


class CommandRegistry:
    def __init__(self) -> None:
        self._commands: dict[str, CommandSpec] = {}

    def register(self, spec: CommandSpec) -> None:
        if not spec.name.startswith("/"):
            raise ValueError("命令名称必须以 / 开头")
        if spec.name in self._commands:
            raise ValueError(f"命令重复：{spec.name}")
        self._commands[spec.name] = spec

    def dispatch(self, line: str) -> tuple[bool, str | None]:
        try:
            parts = shlex.split(line, posix=False)
        except ValueError as exc:
            return True, f"命令参数无法解析：{exc}"
        parts = [unquote(part) for part in parts]
        if not parts or not parts[0].startswith("/"):
            return False, None

        spec = self._commands.get(parts[0].lower())
        if spec is None:
            return False, None
        return True, spec.handler(parts[1:])

    def help_text(self) -> str:
        rows = ["可用命令："]
        for spec in self._commands.values():
            rows.append(
                f"  {spec.usage:<28} {spec.description}"
            )
        return "\n".join(rows)