import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

from aiohttp.test_utils import TestServer
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from helium_mcp.tab_groups import ExtensionBroker


class MCPIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_stdio_advertises_and_calls_native_tools_without_cdp(self):
        token = "mcp-test-" + "x" * 32
        broker = ExtensionBroker(token, 0)
        async with TestServer(broker.application(), host="127.0.0.1") as server:
            broker.port = server.port
            with tempfile.TemporaryDirectory() as directory:
                Path(directory, "bridge.json").write_text(json.dumps({"port": server.port, "token": token}))
                parameters = StdioServerParameters(
                    command=sys.executable,
                    args=["-m", "helium_mcp.server"],
                    env={**os.environ, "HELIUM_MCP_CONFIG_DIR": directory},
                )
                async with stdio_client(parameters) as streams, ClientSession(*streams) as session:
                    initialized = await session.initialize()
                    self.assertIsNotNone(initialized.capabilities.tools)
                    names = {tool.name for tool in (await session.list_tools()).tools}
                    self.assertIn("ensure_pr_tab", names)
                    self.assertIn("get_title", names)
                    result = await session.call_tool("tab_group_status", {})
                    self.assertFalse(json.loads(result.content[0].text)["connected"])
                    result = await session.call_tool("get_title", {})
                    self.assertIn("Not connected", result.content[0].text)


if __name__ == "__main__":
    unittest.main()
