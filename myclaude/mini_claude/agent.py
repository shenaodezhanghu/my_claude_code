import json
from mini_claude.model import create_client, get_models
from mini_claude.tools import create_default_registry, create_tool_context
from mini_claude.prompt import build_system_prompt
from dataclasses import dataclass
import time
from mini_claude.retry import with_retry
from mini_claude.permissions import check_permission


@dataclass
class StreamResult:
    message: dict
    finish_reason: str


class MINI_CLUE_AGENT:
    def __init__(self, permission_mode: str = "default") -> None:
        self.client = create_client()
        self.model = get_models()
        self.messages: list[dict] = []
        self.tools = create_default_registry()
        self.tool_context = create_tool_context()
        self.permission_mode = permission_mode
        self.confirmed_actions: set[str] = set()


    def _confirm(self, message: str) -> bool:
        try:
            answer = input(f"\n允许执行 {message!r}？[y/N] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            return False
        return answer in {"y", "yes"}


    def chat(self, user_text: str) -> str:
        self.messages.append({"role": "user", "content": user_text})
        while True:
            result = with_retry(self._call_model_stream)
            message = result.message
            self.messages.append(message)

            tool_calls = message.get("tool_calls") or []
            if not tool_calls:
                return str(message.get("content") or "")

            for tool_call in tool_calls:
                name = tool_call["function"]["name"]
                try:
                    arguments = json.loads(tool_call["function"]["arguments"])
                except json.JSONDecodeError as exc:
                    tool_result = f"Error: invalid tool arguments: {exc}"
                else:
                    print(f"-> {name}: {arguments}")

                    permission = check_permission(
                        name,
                        arguments,
                        self.permission_mode,
                    )

                    if permission.action == "deny":
                        tool_result = f"Action denied: {permission.message}"

                    elif permission.action == "confirm":
                        key = f"{name}:{permission.message}"
                        allowed = (
                                key in self.confirmed_actions
                                or self._confirm(permission.message)
                        )

                        if allowed:
                            self.confirmed_actions.add(key)
                            tool_result = self.tools.execute(
                                name,
                                arguments,
                                self.tool_context,
                            )
                        else:
                            tool_result = "User denied this action."

                    else:
                        tool_result = self.tools.execute(
                            name,
                            arguments,
                            self.tool_context,
                        )

                self.messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call["id"],
                        "content": tool_result,
                    }
                )


    def history(self) -> list[dict]:
        return list(self.messages)

    def load_history(self, messages: list[dict]) -> None:
        self.messages = list(messages)

    def clear_history(self) -> None:
        self.messages = []

    def print_smooth(self, text: str, delay: float = 0.01) -> None:
        for char in text:
            print(char, end="", flush=True)
            time.sleep(delay)



    def _call_model_stream(self) -> StreamResult:
        stream = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": build_system_prompt()},
                *self.messages,
            ],
            tools=self.tools.schemas(),
            stream=True,
        )

        content_parts: list[str] = []
        tool_calls: dict[int, dict] = {}
        finish_reason = "stop"
        print("助手：")
        for chunk in stream:
            if not chunk.choices:
                continue

            choice = chunk.choices[0]
            delta = choice.delta

            if delta.content:
                self.print_smooth(delta.content)
                content_parts.append(delta.content)

            if delta.tool_calls:
                for part in delta.tool_calls:
                    current = tool_calls.setdefault(
                        part.index,
                        {"id": "", "name": "", "arguments": ""},
                    )
                    if part.id:
                        current["id"] = part.id
                    if part.function and part.function.name:
                        current["name"] += part.function.name
                    if part.function and part.function.arguments:
                        current["arguments"] += part.function.arguments

            if choice.finish_reason:
                finish_reason = choice.finish_reason

        content = "".join(content_parts)
        assembled_calls = [
            {
                "id": item["id"],
                "type": "function",
                "function": {
                    "name": item["name"],
                    "arguments": item["arguments"],
                },
            }
            for _, item in sorted(tool_calls.items())
        ]

        message: dict = {
            "role": "assistant",
            "content": content or None,
        }
        if assembled_calls:
            message["tool_calls"] = assembled_calls

        if content:
            print()

        return StreamResult(message=message, finish_reason=finish_reason)


