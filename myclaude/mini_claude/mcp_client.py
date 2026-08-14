from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import json
import os
import re
import subprocess
import threading


@dataclass(frozen=True)
class McpServerConfig:
    name: str
    command: str
    args: list[str]

# 通用配置解析
def load_mcp_config(
    environ: Mapping[str, str] | None = None,
) -> McpServerConfig | None:
    source = os.environ if environ is None else environ
    command = source.get("MINI_MCP_COMMAND", "").strip()
    if not command:
        return None

    name = source.get("MINI_MCP_NAME", "external").strip() or "external"
    if re.fullmatch(r"[A-Za-z0-9_-]+", name) is None:
        raise ValueError(
            "MINI_MCP_NAME 只能包含字母、数字、下划线和连字符"
        )

    raw_args = source.get("MINI_MCP_ARGS", "[]")
    try:
        args = json.loads(raw_args)
    except json.JSONDecodeError as exc:
        raise ValueError("MINI_MCP_ARGS 必须是 JSON 字符串数组") from exc
    if not isinstance(args, list) or not all(
        isinstance(item, str) for item in args
    ):
        raise ValueError("MINI_MCP_ARGS 必须是 JSON 字符串数组")

    return McpServerConfig(name=name, command=command, args=args)


class McpConnection:
    def __init__(self, command: str, args: list[str]) -> None:
        self.proc = subprocess.Popen(
            [command, *args],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
            encoding='utf-8',
            errors='replace',
            bufsize=1,
        )
        self.id = 0
        self._lock = threading.Lock()
        self.tools: list[dict] = []

# 向 MCP Server 发送一个请求，然后等待服务器回复
    def _request(self, method: str, params: dict | None = None) -> dict:
        with self._lock:
            self.id += 1
            request_id = self.id
            request = {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": method,
                "params": params or {},
            }

            assert self.proc.stdout is not None
            assert self.proc.stdin is not None

            self.proc.stdin.write(json.dumps(request) + "\n")
            self.proc.stdin.flush()

            while True:
                line = self.proc.stdout.readline()
                if not line:
                    raise RuntimeError("MCP Server 已停止")
                try:
                    response = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if response.get("id") == request_id:
                    if "error" in response:
                        raise RuntimeError(
                            f"MCP error: {response['error']}"
                        )
                    return response

    def _notify(self, method: str) -> None:
        assert self.proc.stdin is not None
        self.proc.stdin.write(
            json.dumps({"jsonrpc": "2.0", "method": method}) + "\n"
        )
        self.proc.stdin.flush()

    def connect(self) -> "McpConnection":
        self._request(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {
                    "name": "mini-claude",
                    "version": "1.0",
                },
            },
        )
        self._notify("notifications/initialized")
        listed = self._request("tools/list")
        self.tools = listed.get("result", {}).get("tools", [])
        return self

    def call_tool(self, name: str, arguments: dict) -> str:
        response = self._request(
            "tools/call",
            {"name": name, "arguments": arguments},
        )
        result = response.get("result", {})
        content = result.get("content", [])
        text = "\n".join(
            item.get("text", "")
            for item in content
            if item.get("type") == "text"
        )
        return text or json.dumps(result, ensure_ascii=False)

    def close(self) -> None:
        if self.proc.poll() is None:
            self.proc.terminate()

def connect_mcp(command: str, args: list[str]) -> McpConnection:
    return McpConnection(command, args).connect()
