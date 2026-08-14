from __future__ import annotations

from mini_claude.mcp_client import McpConnection

from .base import Tool, ToolContext


class McpProxyTool(Tool):
    def __init__(
        self,
        server_name: str,
        definition: dict,
        connection: McpConnection,
    ) -> None:
        self.server_name = server_name
        self.remote_name = str(definition["name"])
        self.definition = definition
        self.connection = connection
        super().__init__(
            f"mcp__{server_name}__{self.remote_name}",
            str(definition.get("description") or "MCP external tool"),
        )

    def parameters(self) -> dict:
        return self.definition.get("inputSchema") or {
            "type": "object",
            "properties": {},
        }

    def run(self, args: dict, context: ToolContext) -> str:
        return self.connection.call_tool(self.remote_name, args)