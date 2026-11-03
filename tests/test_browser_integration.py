import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from aiohttp.test_utils import TestServer

from helium_mcp.tab_groups import ExtensionBroker, TabGroupBridge, prepare_extension


@unittest.skipUnless(os.environ.get("HELIUM_TEST_EXECUTABLE"), "Set HELIUM_TEST_EXECUTABLE to test an isolated browser profile")
class BrowserIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_native_groups_and_pr_reuse_in_a_real_browser(self):
        from playwright.async_api import async_playwright

        token = "browser-test-" + "x" * 32
        broker = ExtensionBroker(token, 0)
        async with TestServer(broker.application(), host="127.0.0.1") as server:
            broker.port = server.port
            settings = {"port": server.port, "token": token}
            with tempfile.TemporaryDirectory(prefix="helium-mcp-test-", dir=os.environ.get("HELIUM_TEST_TEMP_DIR")) as directory:
                with patch.dict(os.environ, {"HELIUM_MCP_CONFIG_DIR": str(Path(directory) / "config")}):
                    extension = prepare_extension(settings)
                profile = str(Path(directory) / "profile")
                async with async_playwright() as playwright:
                    context = await playwright.chromium.launch_persistent_context(
                        profile,
                        executable_path=os.environ["HELIUM_TEST_EXECUTABLE"],
                        headless=True,
                        args=[f"--disable-extensions-except={extension}", f"--load-extension={extension}"],
                    )
                    try:
                        await context.route("https://github.com/**", lambda route: route.fulfill(content_type="text/html", body="<title>Test PR</title>"))
                        worker = context.service_workers[0] if context.service_workers else await context.wait_for_event("serviceworker")
                        await asyncio.wait_for(broker.ready.wait(), 15)
                        async with TabGroupBridge(settings) as bridge:
                            url = "https://github.com/example/test/pull/123"
                            await worker.evaluate("url => chrome.tabs.create({url, active: false})", f"{url}/changes#diff-test")
                            before = await bridge.invoke("list_tabs_with_groups", {})
                            first = await bridge.invoke("ensure_pr_tab", {"url": url, "group_title": "Integration test"})
                            self.assertFalse(first["created_tab"])
                            self.assertTrue(first["created_group"])
                            self.assertEqual(first["group"]["title"], "Integration test")
                            self.assertEqual(first["tab"]["group_id"], first["group"]["id"])
                            second = await bridge.invoke("ensure_pr_tab", {"url": f"{url}/files?x=1#diff", "group_title": "Integration test"})
                            self.assertFalse(second["created_tab"])
                            self.assertFalse(second["created_group"])
                            self.assertEqual(second["tab"]["id"], first["tab"]["id"])
                            after = await bridge.invoke("list_tabs_with_groups", {})
                            self.assertEqual(len(before), len(after))
                            self.assertEqual([tab["id"] for tab in before if tab["active"]], [tab["id"] for tab in after if tab["active"]])

                            missing = await bridge.invoke("ensure_pr_tab", {"url": "https://github.com/example/test/pull/124", "group_title": "Integration test"})
                            self.assertTrue(missing["created_tab"])
                            self.assertFalse(missing["created_group"])
                            self.assertEqual(missing["group"]["id"], first["group"]["id"])
                            await bridge.invoke("update_tab_group", {"group_id": first["group"]["id"], "title": "Renamed", "color": "purple", "collapsed": True})
                            groups = await bridge.invoke("list_tab_groups", {})
                            self.assertEqual(groups[0]["title"], "Renamed")
                            self.assertEqual(groups[0]["color"], "purple")
                            self.assertTrue(groups[0]["collapsed"])

                            await bridge.invoke("remove_tabs_from_group", {"tab_ids": [first["tab"]["id"], missing["tab"]["id"]]})
                            self.assertEqual(await bridge.invoke("list_tab_groups", {}), [])
                            created = await bridge.invoke("create_tab_group", {"tab_ids": [first["tab"]["id"]], "title": "New group"})
                            added = await bridge.invoke("add_tabs_to_group", {"group_id": created["id"], "tab_ids": [missing["tab"]["id"]]})
                            self.assertEqual(set(added["tab_ids"]), {first["tab"]["id"], missing["tab"]["id"]})

                            other_window = await worker.evaluate("chrome.windows.create({url: 'about:blank', focused: false})")
                            moved = await bridge.invoke("ensure_pr_tab", {"url": url, "group_title": "Other window", "window_id": other_window["id"]})
                            self.assertFalse(moved["created_tab"])
                            self.assertEqual(moved["tab"]["id"], first["tab"]["id"])
                            self.assertEqual(moved["group"]["window_id"], other_window["id"])
                            self.assertEqual(len(await bridge.invoke("list_tabs_with_groups", {})), len(before) + 2)
                    finally:
                        await context.close()


if __name__ == "__main__":
    unittest.main()
