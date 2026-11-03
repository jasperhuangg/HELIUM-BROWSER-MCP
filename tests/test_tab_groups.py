import asyncio
import json
import os
import socket
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import aiohttp
from aiohttp.test_utils import TestClient, TestServer

from helium_mcp.tab_groups import (
    BRIDGE_NAME,
    TOOLS,
    ExtensionBroker,
    TabGroupBridge,
    load_settings,
    prepare_extension,
)

TOKEN = "test-token-" + "x" * 32
ORIGIN = "chrome-extension://" + "a" * 32


class SettingsTests(unittest.TestCase):
    def test_token_is_private_and_shared_between_clients(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"HELIUM_MCP_CONFIG_DIR": directory}):
            first = load_settings()
            self.assertEqual(first, load_settings())
            self.assertGreaterEqual(len(first["token"]), 32)
            self.assertEqual(stat.S_IMODE((Path(directory) / "bridge.json").stat().st_mode), 0o600)

    def test_invalid_settings_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"HELIUM_MCP_CONFIG_DIR": directory}):
            path = Path(directory) / "bridge.json"
            path.write_text(json.dumps({"port": "37454", "token": TOKEN}))
            with self.assertRaisesRegex(ValueError, "port"):
                load_settings()
            path.write_text(json.dumps({"port": 37454, "token": "short"}))
            with self.assertRaisesRegex(ValueError, "token"):
                load_settings()

    def test_prepared_extension_has_private_local_pairing_details(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"HELIUM_MCP_CONFIG_DIR": directory}):
            settings = load_settings()
            extension = prepare_extension(settings)
            self.assertTrue((extension / "manifest.json").is_file())
            self.assertEqual(json.loads((extension / "pairing.json").read_text()), settings)
            self.assertEqual(stat.S_IMODE((extension / "pairing.json").stat().st_mode), 0o600)


class BrokerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.broker = ExtensionBroker(TOKEN, 0, timeout=0.1)
        self.server = TestServer(self.broker.application(), host="127.0.0.1")
        self.client = TestClient(self.server)
        await self.client.start_server()
        self.broker.port = self.server.port
        self.headers = {"Authorization": f"Bearer {TOKEN}"}

    async def asyncTearDown(self):
        await self.client.close()

    async def pair(self):
        socket = await self.client.ws_connect("/extension", headers={"Origin": ORIGIN})
        await socket.send_json({"token": TOKEN})
        self.assertEqual(await socket.receive_json(), {"type": "ready"})
        return socket

    async def test_authentication_and_dns_rebinding_guards(self):
        for headers, status in [({}, 401), ({"Authorization": "Bearer wrong"}, 401), ({**self.headers, "Origin": "https://evil.test"}, 403), ({**self.headers, "Host": "evil.test"}, 403)]:
            response = await self.client.get("/status", headers=headers)
            self.assertEqual(response.status, status)
        response = await self.client.get("/status", headers=self.headers)
        self.assertEqual(await response.json(), {"name": BRIDGE_NAME, "connected": False})

    async def test_websites_cannot_connect_as_extensions(self):
        with self.assertRaises(aiohttp.WSServerHandshakeError) as error:
            await self.client.ws_connect("/extension", headers={"Origin": "https://evil.test"})
        self.assertEqual(error.exception.status, 403)

    async def test_bad_token_is_rejected_and_a_second_browser_cannot_take_over(self):
        socket = await self.client.ws_connect("/extension", headers={"Origin": ORIGIN})
        await socket.send_json({"token": "wrong"})
        self.assertEqual((await socket.receive()).data, 1008)
        paired = await self.pair()
        second = await self.client.ws_connect("/extension", headers={"Origin": ORIGIN})
        await second.send_json({"token": TOKEN})
        self.assertEqual((await second.receive()).data, 1008)
        self.assertFalse(paired.closed)

    async def test_native_requests_and_responses_are_forwarded(self):
        socket = await self.pair()
        task = asyncio.create_task(self.client.post("/call", json={"method": "ensure_pr_tab", "arguments": {"url": "https://github.com/a/b/pull/1", "group_title": "Task"}}, headers=self.headers))
        request = await socket.receive_json()
        self.assertEqual(request["method"], "ensure_pr_tab")
        await socket.send_json({"id": request["id"], "result": {"created_tab": False}})
        self.assertEqual((await (await task).json())["result"], {"created_tab": False})

    async def test_unknown_methods_and_missing_extension_fail_clearly(self):
        response = await self.client.post("/call", json={"method": "evaluate_js", "arguments": {}}, headers=self.headers)
        self.assertEqual(response.status, 400)
        response = await self.client.post("/call", json={"method": "list_tab_groups", "arguments": {}}, headers=self.headers)
        self.assertEqual(response.status, 503)
        self.assertIn("not connected", (await response.json())["error"])

    async def test_malformed_requests_are_rejected(self):
        for payload in [[], {"method": []}, {"method": "list_tab_groups", "arguments": []}]:
            response = await self.client.post("/call", json=payload, headers=self.headers)
            self.assertEqual(response.status, 400)
        response = await self.client.post("/call", data="not json", headers=self.headers)
        self.assertEqual(response.status, 400)

    async def test_parallel_requests_are_serialized(self):
        socket = await self.pair()
        payload = {"method": "ensure_pr_tab", "arguments": {}}
        tasks = [asyncio.create_task(self.client.post("/call", json=payload, headers=self.headers)) for _ in range(2)]
        first = await socket.receive_json()
        self.assertEqual(len(self.broker.pending), 1)
        await socket.send_json({"id": first["id"], "result": 1})
        second = await socket.receive_json()
        self.assertNotEqual(first["id"], second["id"])
        await socket.send_json({"id": second["id"], "result": 2})
        results = [await response.json() for response in await asyncio.gather(*tasks)]
        self.assertEqual({result["result"] for result in results}, {1, 2})

    async def test_disconnect_reports_uncertain_outcome_and_allows_reconnection(self):
        socket = await self.pair()
        task = asyncio.create_task(self.client.post("/call", json={"method": "ensure_pr_tab", "arguments": {}}, headers=self.headers))
        await socket.receive_json()
        await socket.close()
        response = await task
        self.assertEqual(response.status, 504)
        self.assertIn("may have completed", (await response.json())["error"])
        await self.pair()

    async def test_multiple_mcp_clients_reuse_the_bridge(self):
        settings = {"port": self.server.port, "token": TOKEN}
        async with TabGroupBridge(settings) as first, TabGroupBridge(settings) as second:
            self.assertIsNone(first.runner)
            self.assertIsNone(second.runner)
            self.assertEqual(await first.invoke("tab_group_status", {}), {"name": BRIDGE_NAME, "connected": False})

    async def test_another_chat_takes_over_when_the_bridge_owner_exits(self):
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        settings = {"port": port, "token": TOKEN}
        first = TabGroupBridge(settings)
        async with first, TabGroupBridge(settings) as second:
            await first.__aexit__()
            result = await second.invoke("tab_group_status", {})
            self.assertEqual(result["name"], BRIDGE_NAME)
            self.assertIsNotNone(second.runner)

    async def test_tools_expose_native_ids_and_idempotent_pr_workflow(self):
        tools = {tool.name: tool for tool in TOOLS}
        self.assertIn("NOT CDP", tools["list_tabs_with_groups"].description)
        self.assertEqual(tools["ensure_pr_tab"].inputSchema["required"], ["url", "group_title"])


if __name__ == "__main__":
    unittest.main()
