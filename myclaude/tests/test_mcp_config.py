from __future__ import annotations

import unittest

from mini_claude.mcp_client import McpServerConfig, load_mcp_config


class LoadMcpConfigTests(unittest.TestCase):
    def test_returns_none_without_command(self) -> None:
        self.assertIsNone(load_mcp_config({}))

    def test_loads_generic_command_and_arguments(self) -> None:
        config = load_mcp_config(
            {
                "MINI_MCP_NAME": "filesystem",
                "MINI_MCP_COMMAND": "cmd",
                "MINI_MCP_ARGS": (
                    '["/c", "npx", "-y", '
                    '"@modelcontextprotocol/server-filesystem", "."]'
                ),
            }
        )

        self.assertEqual(
            config,
            McpServerConfig(
                name="filesystem",
                command="cmd",
                args=[
                    "/c",
                    "npx",
                    "-y",
                    "@modelcontextprotocol/server-filesystem",
                    ".",
                ],
            ),
        )

    def test_rejects_non_string_arguments(self) -> None:
        with self.assertRaisesRegex(ValueError, "JSON 字符串数组"):
            load_mcp_config(
                {
                    "MINI_MCP_COMMAND": "node",
                    "MINI_MCP_ARGS": '["server.mjs", 1]',
                }
            )

    def test_rejects_invalid_server_name(self) -> None:
        with self.assertRaisesRegex(ValueError, "MINI_MCP_NAME"):
            load_mcp_config(
                {
                    "MINI_MCP_NAME": "github server",
                    "MINI_MCP_COMMAND": "docker",
                }
            )


if __name__ == "__main__":
    unittest.main()
