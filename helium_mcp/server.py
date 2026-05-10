import asyncio
import base64
import json
import time
from typing import Any, Optional

import aiohttp
import websockets
from mcp.server import InitializationOptions, Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool


class HeliumCDP:
    def __init__(self):
        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._reader_task: Optional[asyncio.Task] = None
        self._msg_id = 0
        self._pending: dict[int, asyncio.Future] = {}
        self._target_id: Optional[str] = None
        self._ws_url: Optional[str] = None
        self.host = "localhost"
        self.port = 9222
        self._console_logs: list[dict] = []
        self._network_logs: list[dict] = []
        self._dialog: Optional[dict] = None

    async def connect(self, host: str = "localhost", port: int = 9222):
        self.host = host
        self.port = port
        async with aiohttp.ClientSession() as session:
            async with session.get(f"http://{host}:{port}/json/list") as resp:
                tabs = await resp.json()
                if not tabs:
                    raise Exception("No browser tabs available")
                tab = tabs[0]
                self._ws_url = tab["webSocketDebuggerUrl"]
                self._target_id = tab["id"]
        self._ws = await websockets.connect(self._ws_url)
        self._reader_task = asyncio.create_task(self._reader())
        await self._enable_domains()
        return self._target_id

    async def _enable_domains(self):
        await self.send("Page.enable")
        await self.send("DOM.enable")
        await self.send("Runtime.enable")
        await self.send("Console.enable")
        await self.send("Network.enable")

    async def _reader(self):
        while True:
            try:
                msg = await self._ws.recv()
                data = json.loads(msg)
                msg_id = data.get("id")
                if msg_id is not None and msg_id in self._pending:
                    self._pending.pop(msg_id).set_result(data)
                elif "method" in data:
                    method = data["method"]
                    params = data.get("params", {})
                    if method == "Console.messageAdded":
                        m = params.get("message", {})
                        self._console_logs.append({
                            "level": m.get("level", "log"),
                            "text": m.get("text", ""),
                            "source": m.get("source", ""),
                            "url": m.get("url", ""),
                            "line": m.get("lineNumber", 0),
                        })
                    elif method == "Network.requestWillBeSent":
                        req = params.get("request", {})
                        self._network_logs.append({
                            "type": "request",
                            "requestId": params.get("requestId"),
                            "url": req.get("url", ""),
                            "method": req.get("method", ""),
                            "headers": req.get("headers", {}),
                            "timestamp": params.get("timestamp"),
                        })
                    elif method == "Network.responseReceived":
                        resp = params.get("response", {})
                        self._network_logs.append({
                            "type": "response",
                            "requestId": params.get("requestId"),
                            "url": resp.get("url", ""),
                            "status": resp.get("status"),
                            "statusText": resp.get("statusText", ""),
                            "headers": resp.get("headers", {}),
                            "timestamp": params.get("timestamp"),
                        })
                    elif method == "Page.javascriptDialogOpening":
                        self._dialog = {
                            "type": params.get("type"),
                            "message": params.get("message"),
                            "defaultPrompt": params.get("defaultPrompt", ""),
                        }
            except websockets.ConnectionClosed:
                break
            except Exception:
                continue

    async def send(self, method: str, params: dict = None, timeout: float = 30.0):
        if self._ws is None:
            raise Exception("Not connected to browser")
        self._msg_id += 1
        msg_id = self._msg_id
        future = asyncio.get_event_loop().create_future()
        self._pending[msg_id] = future
        await self._ws.send(json.dumps({
            "id": msg_id,
            "method": method,
            "params": params or {}
        }))
        result = await asyncio.wait_for(future, timeout=timeout)
        if "error" in result:
            raise Exception(f"CDP error: {result['error']}")
        return result

    async def navigate(self, url: str):
        return await self.send("Page.navigate", {"url": url})

    async def reload(self):
        return await self.send("Page.reload")

    async def go_back(self):
        result = await self.send("Page.getNavigationHistory")
        entries = result["result"]["entries"]
        idx = result["result"]["currentIndex"]
        if idx > 0:
            return await self.send("Page.navigateToHistoryEntry", {"entryId": entries[idx - 1]["id"]})
        raise Exception("No previous page in history")

    async def go_forward(self):
        result = await self.send("Page.getNavigationHistory")
        entries = result["result"]["entries"]
        idx = result["result"]["currentIndex"]
        if idx < len(entries) - 1:
            return await self.send("Page.navigateToHistoryEntry", {"entryId": entries[idx + 1]["id"]})
        raise Exception("No next page in history")

    async def get_html(self):
        result = await self.send("DOM.getDocument", {"depth": -1})
        root = result["result"]["root"]["nodeId"]
        result = await self.send("DOM.getOuterHTML", {"nodeId": root})
        return result["result"]["outerHTML"]

    async def get_text(self):
        result = await self.send("Runtime.evaluate", {
            "expression": "document.body?.innerText || document.documentElement.innerText",
            "returnByValue": True,
        })
        return result["result"]["result"]["value"]

    async def get_url(self):
        result = await self.send("Runtime.evaluate", {
            "expression": "window.location.href",
            "returnByValue": True,
        })
        return result["result"]["result"]["value"]

    async def get_title(self):
        result = await self.send("Runtime.evaluate", {
            "expression": "document.title",
            "returnByValue": True,
        })
        return result["result"]["result"]["value"]

    async def screenshot(self, full_page: bool = True):
        if full_page:
            dims = await self.send("Runtime.evaluate", {
                "expression": "({w: Math.max(document.documentElement.scrollWidth, document.body.scrollWidth), h: Math.max(document.documentElement.scrollHeight, document.body.scrollHeight)})",
                "returnByValue": True,
            })
            d = dims["result"]["result"]["value"]
            await self.send("Emulation.setDeviceMetricsOverride", {
                "width": int(d["w"]), "height": int(d["h"]),
                "deviceScaleFactor": 1, "mobile": False,
            })
            await asyncio.sleep(0.2)
        result = await self.send("Page.captureScreenshot", {"format": "png", "fromSurface": True})
        if full_page:
            await self.send("Emulation.clearDeviceMetricsOverride")
        return result["result"]["data"]

    async def get_pdf(self):
        result = await self.send("Page.printToPDF", {
            "printBackground": True,
            "preferCSSPageSize": True,
        })
        return result["result"]["data"]

    async def get_viewport(self):
        result = await self.send("Runtime.evaluate", {
            "expression": "({width: window.innerWidth, height: window.innerHeight, dpr: window.devicePixelRatio})",
            "returnByValue": True,
        })
        return result["result"]["result"]["value"]

    async def set_viewport(self, width: int, height: int):
        return await self.send("Emulation.setDeviceMetricsOverride", {
            "width": width, "height": height,
            "deviceScaleFactor": 1, "mobile": False,
        })

    async def get_accessibility_tree(self):
        try:
            result = await self.send("Accessibility.getFullAXTree", {"depth": -1})
            return result["result"]
        except Exception:
            result = await self.send("Runtime.evaluate", {
                "expression": """(() => {
                    function walk(n, d) {
                        if (d > 10) return [];
                        const r = n.getAttribute('role') || n.tagName?.toLowerCase() || '';
                        const nm = n.getAttribute('aria-label') || n.textContent?.trim()?.substring(0,100) || '';
                        const s = {};
                        ['checked','selected','disabled','expanded','hidden','pressed'].forEach(a => {
                            const v = n.getAttribute('aria-' + a);
                            if (v !== null) s[a] = v;
                        });
                        const items = [{role:r, name:nm, states:s, depth:d, tag:n.tagName?.toLowerCase()}];
                        for (const c of n.children) items.push(...walk(c, d+1));
                        return items;
                    }
                    return walk(document.body, 0);
                })()""",
                "returnByValue": True,
            })
            return {"nodes": result["result"]["result"]["value"]}

    async def _find_node(self, selector: str) -> int:
        result = await self.send("DOM.getDocument", {"depth": -1})
        root = result["result"]["root"]["nodeId"]
        result = await self.send("DOM.querySelector", {"nodeId": root, "selector": selector})
        if "nodeId" not in result["result"]:
            raise Exception(f"Element not found: {selector}")
        return result["result"]["nodeId"]

    async def _node_center(self, node_id: int):
        box = await self.send("DOM.getBoxModel", {"nodeId": node_id})
        c = box["result"]["model"]["content"]
        return (c[0] + c[2]) / 2, (c[1] + c[3]) / 2

    async def click_element(self, selector: str):
        node_id = await self._find_node(selector)
        await self.send("DOM.scrollIntoViewIfNeeded", {"nodeId": node_id})
        await asyncio.sleep(0.1)
        x, y = await self._node_center(node_id)
        await self.send("Input.dispatchMouseEvent", {
            "type": "mousePressed", "x": x, "y": y,
            "button": "left", "clickCount": 1,
        })
        await asyncio.sleep(0.05)
        return await self.send("Input.dispatchMouseEvent", {
            "type": "mouseReleased", "x": x, "y": y,
            "button": "left", "clickCount": 1,
        })

    async def fill_input(self, selector: str, value: str):
        node_id = await self._find_node(selector)
        await self.send("DOM.focus", {"nodeId": node_id})
        escaped = selector.replace("\\", "\\\\").replace("'", "\\'")
        await self.send("Runtime.evaluate", {
            "expression": f"""
            (() => {{
                const el = document.querySelector('{escaped}');
                if (el) {{ el.value = ''; el.dispatchEvent(new Event('input', {{bubbles:true}})); el.dispatchEvent(new Event('change', {{bubbles:true}})); }}
            }})()
            """
        })
        await self.send("Input.insertText", {"text": value})
        await self.send("Runtime.evaluate", {
            "expression": f"""
            (() => {{
                const el = document.querySelector('{escaped}');
                if (el) el.dispatchEvent(new Event('change', {{bubbles:true}}));
            }})()
            """
        })
        return {"success": True}

    async def type_text(self, selector: str, text: str, delay_ms: int = 50):
        node_id = await self._find_node(selector)
        await self.send("DOM.focus", {"nodeId": node_id})
        escaped = selector.replace("\\", "\\\\").replace("'", "\\'")
        await self.send("Runtime.evaluate", {
            "expression": f"""
            (() => {{
                const el = document.querySelector('{escaped}');
                if (el) {{ el.value = ''; el.dispatchEvent(new Event('input', {{bubbles:true}})); }}
            }})()
            """
        })
        for char in text:
            await self.send("Input.dispatchKeyEvent", {
                "type": "keyDown", "windowsVirtualKeyCode": ord(char),
                "text": char, "key": char,
            })
            if delay_ms:
                await asyncio.sleep(delay_ms / 1000)
            await self.send("Input.dispatchKeyEvent", {
                "type": "keyUp", "windowsVirtualKeyCode": ord(char),
                "text": char, "key": char,
            })
            if delay_ms:
                await asyncio.sleep(delay_ms / 1000)
        return {"success": True}

    async def hover_element(self, selector: str):
        node_id = await self._find_node(selector)
        await self.send("DOM.scrollIntoViewIfNeeded", {"nodeId": node_id})
        x, y = await self._node_center(node_id)
        return await self.send("Input.dispatchMouseEvent", {
            "type": "mouseMoved", "x": x, "y": y,
        })

    async def select_option(self, selector: str, values: list[str]):
        escaped = selector.replace("\\", "\\\\").replace("'", "\\'")
        vjson = json.dumps(values)
        result = await self.send("Runtime.evaluate", {
            "expression": f"""
            (() => {{
                const el = document.querySelector('{escaped}');
                if (!el || !el.options) return false;
                Array.from(el.options).forEach(o => o.selected = false);
                {vjson}.forEach(v => {{
                    const o = Array.from(el.options).find(o => o.value === v || o.text === v);
                    if (o) o.selected = true;
                }});
                el.dispatchEvent(new Event('change', {{bubbles:true}}));
                return true;
            }})()
            """,
            "returnByValue": True,
        })
        return {"success": result["result"]["result"]["value"]}

    async def focus_element(self, selector: str):
        node_id = await self._find_node(selector)
        await self.send("DOM.focus", {"nodeId": node_id})
        return {"success": True}

    async def scroll_page(self, x: int = 0, y: int = 0, selector: Optional[str] = None):
        if selector:
            escaped = selector.replace("\\", "\\\\").replace("'", "\\'")
            await self.send("Runtime.evaluate", {
                "expression": f"""
                (() => {{
                    const el = document.querySelector('{escaped}');
                    if (el) {{ el.scrollBy({{left:{x}, top:{y}, behavior:'smooth'}}); return true; }}
                    return false;
                }})()
                """,
                "returnByValue": True,
            })
        else:
            await self.send("Runtime.evaluate", {
                "expression": f"window.scrollBy({{left:{x}, top:{y}, behavior:'smooth'}})",
                "returnByValue": True,
            })
        return {"success": True}

    async def get_element_info(self, selector: str):
        escaped = selector.replace("\\", "\\\\").replace("'", "\\'")
        result = await self.send("Runtime.evaluate", {
            "expression": f"""
            (() => {{
                const el = document.querySelector('{escaped}');
                if (!el) return null;
                const r = el.getBoundingClientRect();
                const a = {{}};
                for (const attr of el.attributes || []) a[attr.name] = attr.value;
                return {{
                    tag: el.tagName?.toLowerCase(),
                    text: (el.textContent || '').trim().substring(0, 500),
                    visible: (el.offsetParent !== null || el === document.body) && el.offsetWidth > 0,
                    rect: {{left:r.left, top:r.top, width:r.width, height:r.height}},
                    attrs: a,
                    inner_html: el.innerHTML?.substring(0, 1000) || '',
                    value: el.value !== undefined ? el.value : null,
                    checked: el.checked !== undefined ? el.checked : null,
                    disabled: el.disabled !== undefined ? el.disabled : null,
                    selected_idx: el.selectedIndex !== undefined ? el.selectedIndex : null,
                    class_list: Array.from(el.classList || []),
                }};
            }})()
            """,
            "returnByValue": True,
        })
        value = result["result"]["result"]["value"]
        if value is None:
            raise Exception(f"Element not found: {selector}")
        return value

    async def press_key(self, key: str, modifiers: Optional[list[str]] = None):
        mod_map = {"alt": "altKey", "ctrl": "ctrlKey", "meta": "metaKey", "shift": "shiftKey"}
        mods = {}
        if modifiers:
            for m in modifiers:
                mods[mod_map.get(m, m)] = True
        key_map = {
            "Enter": "Enter", "Tab": "Tab", "Escape": "Escape",
            "Backspace": "Backspace", "Delete": "Delete",
            "ArrowUp": "ArrowUp", "ArrowDown": "ArrowDown",
            "ArrowLeft": "ArrowLeft", "ArrowRight": "ArrowRight",
            "Home": "Home", "End": "End", "PageUp": "PageUp", "PageDown": "PageDown",
        }
        cdp_key = key_map.get(key, key)
        text = key if len(key) == 1 else ""
        await self.send("Input.dispatchKeyEvent", {
            "type": "rawKeyDown", "key": cdp_key, "text": text,
            "windowsVirtualKeyCode": ord(key[0]) if key else 0,
            **mods,
        })
        await asyncio.sleep(0.05)
        return await self.send("Input.dispatchKeyEvent", {
            "type": "keyUp", "key": cdp_key, "text": text,
            **mods,
        })

    async def evaluate_js(self, script: str):
        result = await self.send("Runtime.evaluate", {
            "expression": script,
            "returnByValue": True,
            "awaitPromise": True,
        })
        return result["result"]["result"]

    async def get_cookies(self):
        result = await self.send("Network.getAllCookies")
        return result["result"]["cookies"]

    async def set_cookie(self, name: str, value: str, domain: str = "",
                         path: str = "/", secure: bool = False, http_only: bool = False,
                         same_site: Optional[str] = None):
        params = {
            "name": name, "value": value,
            "path": path, "secure": secure, "httpOnly": http_only,
        }
        if domain:
            params["domain"] = domain
        if same_site:
            params["sameSite"] = same_site
        return await self.send("Network.setCookie", params)

    async def delete_cookie(self, name: str, domain: str):
        return await self.send("Network.deleteCookie", {"name": name, "domain": domain})

    async def clear_cookies(self):
        return await self.send("Network.clearBrowserCookies")

    async def get_console_logs(self):
        logs = list(self._console_logs)
        self._console_logs.clear()
        return logs

    async def get_network_logs(self):
        logs = list(self._network_logs)
        self._network_logs.clear()
        return logs

    async def get_tabs(self):
        async with aiohttp.ClientSession() as session:
            async with session.get(f"http://{self.host}:{self.port}/json/list") as resp:
                tabs = await resp.json()
                return [{
                    "id": t["id"], "url": t.get("url", ""),
                    "title": t.get("title", ""),
                } for t in tabs]

    async def create_tab(self, url: str = "about:blank"):
        async with aiohttp.ClientSession() as session:
            async with session.put(
                f"http://{self.host}:{self.port}/json/new",
                params={"url": url}
            ) as resp:
                tab = await resp.json()
                return tab.get("id", "")

    async def close_tab(self, target_id: str):
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"http://{self.host}:{self.port}/json/close/{target_id}"
            ) as resp:
                return await resp.text()

    async def switch_tab(self, target_id: str):
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"http://{self.host}:{self.port}/json/list"
            ) as resp:
                tabs = await resp.json()
                for tab in tabs:
                    if tab["id"] == target_id:
                        self._ws_url = tab["webSocketDebuggerUrl"]
                        self._target_id = target_id
                        if self._reader_task:
                            self._reader_task.cancel()
                        if self._ws:
                            await self._ws.close()
                        self._pending.clear()
                        self._ws = await websockets.connect(self._ws_url)
                        self._reader_task = asyncio.create_task(self._reader())
                        await self._enable_domains()
                        return {"success": True, "target_id": target_id}
        raise Exception(f"Target not found: {target_id}")

    async def wait(self, ms: int = 1000, selector: Optional[str] = None,
                   text: Optional[str] = None, timeout_ms: int = 10000):
        deadline = time.time() + (timeout_ms / 1000)
        if selector:
            while time.time() < deadline:
                try:
                    await self._find_node(selector)
                    return {"found": True, "selector": selector}
                except Exception:
                    await asyncio.sleep(0.2)
            raise Exception(f"Timeout waiting for selector: {selector}")
        elif text:
            escaped = json.dumps(text)
            while time.time() < deadline:
                result = await self.send("Runtime.evaluate", {
                    "expression": f"document.body.innerText.includes({escaped})",
                    "returnByValue": True,
                })
                if result["result"]["result"]["value"]:
                    return {"found": True, "text": text}
                await asyncio.sleep(0.2)
            raise Exception(f"Timeout waiting for text: {text}")
        else:
            await asyncio.sleep(ms / 1000)
            return {"waited_ms": ms}

    async def handle_dialog(self, accept: bool = True, prompt_text: Optional[str] = None):
        params = {"accept": accept}
        if prompt_text is not None:
            params["promptText"] = prompt_text
        result = await self.send("Page.handleJavaScriptDialog", params)
        self._dialog = None
        return result

    async def get_dialog(self):
        return self._dialog

    async def disconnect(self):
        if self._reader_task:
            self._reader_task.cancel()
            self._reader_task = None
        if self._ws:
            await self._ws.close()
            self._ws = None
        self._pending.clear()
        self._console_logs.clear()
        self._network_logs.clear()
        self._dialog = None


cdp: Optional[HeliumCDP] = None
app = Server("helium-browser")


@app.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="connect",
            description="Connect to Helium/Chrome browser via CDP (must be started with --remote-debugging-port=9222)",
            inputSchema={
                "type": "object",
                "properties": {
                    "host": {"type": "string", "default": "localhost"},
                    "port": {"type": "integer", "default": 9222},
                },
            },
        ),
        Tool(
            name="navigate",
            description="Navigate to a URL",
            inputSchema={
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "URL to navigate to"},
                },
                "required": ["url"],
            },
        ),
        Tool(
            name="reload",
            description="Reload the current page",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="go_back",
            description="Go back in browser history",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="go_forward",
            description="Go forward in browser history",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="click",
            description="Click an element by CSS selector",
            inputSchema={
                "type": "object",
                "properties": {
                    "selector": {"type": "string", "description": "CSS selector for the element"},
                },
                "required": ["selector"],
            },
        ),
        Tool(
            name="fill",
            description="Fill an input field by CSS selector (sets value and triggers events)",
            inputSchema={
                "type": "object",
                "properties": {
                    "selector": {"type": "string", "description": "CSS selector for the input"},
                    "value": {"type": "string", "description": "Value to fill"},
                },
                "required": ["selector", "value"],
            },
        ),
        Tool(
            name="type",
            description="Type text into an element character by character (realistic typing simulation)",
            inputSchema={
                "type": "object",
                "properties": {
                    "selector": {"type": "string", "description": "CSS selector for the input"},
                    "text": {"type": "string", "description": "Text to type"},
                    "delay_ms": {"type": "integer", "default": 50, "description": "Delay between keystrokes in ms"},
                },
                "required": ["selector", "text"],
            },
        ),
        Tool(
            name="hover",
            description="Hover over an element by CSS selector",
            inputSchema={
                "type": "object",
                "properties": {
                    "selector": {"type": "string", "description": "CSS selector for the element"},
                },
                "required": ["selector"],
            },
        ),
        Tool(
            name="select",
            description="Select option(s) in a dropdown by CSS selector",
            inputSchema={
                "type": "object",
                "properties": {
                    "selector": {"type": "string", "description": "CSS selector for the select element"},
                    "values": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Values or labels of options to select",
                    },
                },
                "required": ["selector", "values"],
            },
        ),
        Tool(
            name="focus",
            description="Focus an element by CSS selector",
            inputSchema={
                "type": "object",
                "properties": {
                    "selector": {"type": "string", "description": "CSS selector for the element"},
                },
                "required": ["selector"],
            },
        ),
        Tool(
            name="scroll",
            description="Scroll the page or an element by CSS selector",
            inputSchema={
                "type": "object",
                "properties": {
                    "x": {"type": "integer", "default": 0, "description": "Horizontal scroll offset"},
                    "y": {"type": "integer", "default": 0, "description": "Vertical scroll offset"},
                    "selector": {"type": "string", "description": "CSS selector to scroll (omit to scroll page)"},
                },
            },
        ),
        Tool(
            name="press_key",
            description="Press a keyboard key (Enter, Tab, Escape, ArrowUp, etc.)",
            inputSchema={
                "type": "object",
                "properties": {
                    "key": {"type": "string", "description": "Key to press (e.g. Enter, Tab, Escape, ArrowUp, Backspace, etc.)"},
                    "modifiers": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Modifier keys (alt, ctrl, meta, shift)",
                    },
                },
                "required": ["key"],
            },
        ),
        Tool(
            name="get_html",
            description="Get the current page HTML content",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="get_text",
            description="Get the visible text content of the page",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="get_url",
            description="Get the current page URL",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="get_title",
            description="Get the current page title",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="get_element_info",
            description="Get detailed info (attributes, text, position, visibility) about an element",
            inputSchema={
                "type": "object",
                "properties": {
                    "selector": {"type": "string", "description": "CSS selector for the element"},
                },
                "required": ["selector"],
            },
        ),
        Tool(
            name="screenshot",
            description="Take a screenshot of the current page (returns base64 PNG data URI)",
            inputSchema={
                "type": "object",
                "properties": {
                    "full_page": {"type": "boolean", "default": True, "description": "Capture full page or just viewport"},
                },
            },
        ),
        Tool(
            name="get_pdf",
            description="Export the current page as a PDF (returns base64 data URI)",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="get_viewport",
            description="Get the current viewport size and device pixel ratio",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="set_viewport",
            description="Set the viewport size",
            inputSchema={
                "type": "object",
                "properties": {
                    "width": {"type": "integer", "description": "Viewport width in pixels"},
                    "height": {"type": "integer", "description": "Viewport height in pixels"},
                },
                "required": ["width", "height"],
            },
        ),
        Tool(
            name="get_accessibility_tree",
            description="Get the accessibility tree of the current page for semantic interaction",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="evaluate_js",
            description="Execute JavaScript in the page context",
            inputSchema={
                "type": "object",
                "properties": {
                    "script": {"type": "string", "description": "JavaScript code to execute"},
                },
                "required": ["script"],
            },
        ),
        Tool(
            name="get_cookies",
            description="Get all browser cookies",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="set_cookie",
            description="Set a browser cookie",
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "value": {"type": "string"},
                    "domain": {"type": "string", "description": "Cookie domain"},
                    "path": {"type": "string", "default": "/"},
                    "secure": {"type": "boolean", "default": False},
                    "http_only": {"type": "boolean", "default": False},
                    "same_site": {"type": "string", "enum": ["Strict", "Lax", "None"]},
                },
                "required": ["name", "value"],
            },
        ),
        Tool(
            name="delete_cookie",
            description="Delete a browser cookie by name and domain",
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "domain": {"type": "string"},
                },
                "required": ["name", "domain"],
            },
        ),
        Tool(
            name="clear_cookies",
            description="Clear all browser cookies",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="get_console_logs",
            description="Get console log messages from the page",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="get_network_logs",
            description="Get network request/response logs from the page",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="list_tabs",
            description="List all open browser tabs",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="create_tab",
            description="Create a new browser tab",
            inputSchema={
                "type": "object",
                "properties": {
                    "url": {"type": "string", "default": "about:blank"},
                },
            },
        ),
        Tool(
            name="close_tab",
            description="Close a browser tab by target ID",
            inputSchema={
                "type": "object",
                "properties": {
                    "target_id": {"type": "string", "description": "Tab ID to close"},
                },
                "required": ["target_id"],
            },
        ),
        Tool(
            name="switch_tab",
            description="Switch active connection to a different tab by target ID",
            inputSchema={
                "type": "object",
                "properties": {
                    "target_id": {"type": "string", "description": "Tab ID to switch to"},
                },
                "required": ["target_id"],
            },
        ),
        Tool(
            name="wait",
            description="Wait for a specified time, or for an element or text to appear",
            inputSchema={
                "type": "object",
                "properties": {
                    "ms": {"type": "integer", "default": 1000, "description": "Time to wait in ms (if no selector/text)"},
                    "selector": {"type": "string", "description": "CSS selector to wait for"},
                    "text": {"type": "string", "description": "Text to wait for on the page"},
                    "timeout_ms": {"type": "integer", "default": 10000, "description": "Maximum wait time in ms"},
                },
            },
        ),
        Tool(
            name="handle_dialog",
            description="Accept or dismiss a JavaScript dialog (alert, confirm, prompt)",
            inputSchema={
                "type": "object",
                "properties": {
                    "accept": {"type": "boolean", "default": True, "description": "Accept or dismiss the dialog"},
                    "prompt_text": {"type": "string", "description": "Text to enter for prompt dialogs"},
                },
            },
        ),
        Tool(
            name="get_dialog",
            description="Get the current pending dialog info (if any)",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="disconnect",
            description="Disconnect from the browser",
            inputSchema={"type": "object", "properties": {}},
        ),
    ]


@app.call_tool()
async def call_tool(name: str, arguments: Any) -> list[TextContent]:
    global cdp

    try:
        if name == "connect":
            cdp = HeliumCDP()
            target = await cdp.connect(
                host=arguments.get("host", "localhost"),
                port=arguments.get("port", 9222),
            )
            return [TextContent(
                type="text",
                text=f"Connected to browser (target: {target})"
            )]

        if cdp is None:
            return [TextContent(
                type="text",
                text="Error: Not connected. Run 'connect' first."
            )]

        if name == "navigate":
            r = await cdp.navigate(arguments["url"])
            return [TextContent(type="text", text=json.dumps(r, indent=2))]

        elif name == "reload":
            r = await cdp.reload()
            return [TextContent(type="text", text=json.dumps(r, indent=2))]

        elif name == "go_back":
            r = await cdp.go_back()
            return [TextContent(type="text", text=json.dumps(r, indent=2))]

        elif name == "go_forward":
            r = await cdp.go_forward()
            return [TextContent(type="text", text=json.dumps(r, indent=2))]

        elif name == "click":
            r = await cdp.click_element(arguments["selector"])
            return [TextContent(type="text", text=json.dumps(r, indent=2))]

        elif name == "fill":
            r = await cdp.fill_input(arguments["selector"], arguments["value"])
            return [TextContent(type="text", text=json.dumps(r, indent=2))]

        elif name == "type":
            r = await cdp.type_text(
                arguments["selector"],
                arguments["text"],
                arguments.get("delay_ms", 50),
            )
            return [TextContent(type="text", text=json.dumps(r, indent=2))]

        elif name == "hover":
            r = await cdp.hover_element(arguments["selector"])
            return [TextContent(type="text", text=json.dumps(r, indent=2))]

        elif name == "select":
            r = await cdp.select_option(arguments["selector"], arguments["values"])
            return [TextContent(type="text", text=json.dumps(r, indent=2))]

        elif name == "focus":
            r = await cdp.focus_element(arguments["selector"])
            return [TextContent(type="text", text=json.dumps(r, indent=2))]

        elif name == "scroll":
            r = await cdp.scroll_page(
                arguments.get("x", 0),
                arguments.get("y", 0),
                arguments.get("selector"),
            )
            return [TextContent(type="text", text=json.dumps(r, indent=2))]

        elif name == "press_key":
            r = await cdp.press_key(
                arguments["key"],
                arguments.get("modifiers"),
            )
            return [TextContent(type="text", text=json.dumps(r, indent=2))]

        elif name == "get_html":
            html = await cdp.get_html()
            return [TextContent(type="text", text=html)]

        elif name == "get_text":
            text = await cdp.get_text()
            return [TextContent(type="text", text=text)]

        elif name == "get_url":
            url = await cdp.get_url()
            return [TextContent(type="text", text=url)]

        elif name == "get_title":
            title = await cdp.get_title()
            return [TextContent(type="text", text=title)]

        elif name == "get_element_info":
            info = await cdp.get_element_info(arguments["selector"])
            return [TextContent(type="text", text=json.dumps(info, indent=2))]

        elif name == "screenshot":
            full_page = arguments.get("full_page", True)
            data = await cdp.screenshot(full_page)
            return [TextContent(type="text", text=f"data:image/png;base64,{data}")]

        elif name == "get_pdf":
            data = await cdp.get_pdf()
            return [TextContent(type="text", text=f"data:application/pdf;base64,{data}")]

        elif name == "get_viewport":
            vp = await cdp.get_viewport()
            return [TextContent(type="text", text=json.dumps(vp, indent=2))]

        elif name == "set_viewport":
            r = await cdp.set_viewport(arguments["width"], arguments["height"])
            return [TextContent(type="text", text=json.dumps(r, indent=2))]

        elif name == "get_accessibility_tree":
            tree = await cdp.get_accessibility_tree()
            return [TextContent(type="text", text=json.dumps(tree, indent=2))]

        elif name == "evaluate_js":
            r = await cdp.evaluate_js(arguments["script"])
            return [TextContent(type="text", text=json.dumps(r, indent=2))]

        elif name == "get_cookies":
            cookies = await cdp.get_cookies()
            return [TextContent(type="text", text=json.dumps(cookies, indent=2))]

        elif name == "set_cookie":
            r = await cdp.set_cookie(
                arguments["name"],
                arguments["value"],
                arguments.get("domain", ""),
                arguments.get("path", "/"),
                arguments.get("secure", False),
                arguments.get("http_only", False),
                arguments.get("same_site"),
            )
            return [TextContent(type="text", text=json.dumps(r, indent=2))]

        elif name == "delete_cookie":
            r = await cdp.delete_cookie(arguments["name"], arguments["domain"])
            return [TextContent(type="text", text=json.dumps(r, indent=2))]

        elif name == "clear_cookies":
            r = await cdp.clear_cookies()
            return [TextContent(type="text", text=json.dumps(r, indent=2))]

        elif name == "get_console_logs":
            logs = await cdp.get_console_logs()
            return [TextContent(type="text", text=json.dumps(logs, indent=2))]

        elif name == "get_network_logs":
            logs = await cdp.get_network_logs()
            return [TextContent(type="text", text=json.dumps(logs, indent=2))]

        elif name == "list_tabs":
            tabs = await cdp.get_tabs()
            return [TextContent(type="text", text=json.dumps(tabs, indent=2))]

        elif name == "create_tab":
            tid = await cdp.create_tab(arguments.get("url", "about:blank"))
            return [TextContent(type="text", text=f"Created tab: {tid}")]

        elif name == "close_tab":
            r = await cdp.close_tab(arguments["target_id"])
            return [TextContent(type="text", text=json.dumps(r, indent=2))]

        elif name == "switch_tab":
            r = await cdp.switch_tab(arguments["target_id"])
            return [TextContent(type="text", text=json.dumps(r, indent=2))]

        elif name == "wait":
            r = await cdp.wait(
                arguments.get("ms", 1000),
                arguments.get("selector"),
                arguments.get("text"),
                arguments.get("timeout_ms", 10000),
            )
            return [TextContent(type="text", text=json.dumps(r, indent=2))]

        elif name == "handle_dialog":
            r = await cdp.handle_dialog(
                arguments.get("accept", True),
                arguments.get("prompt_text"),
            )
            return [TextContent(type="text", text=json.dumps(r, indent=2))]

        elif name == "get_dialog":
            d = await cdp.get_dialog()
            return [TextContent(type="text", text=json.dumps(d, indent=2) if d else "No pending dialog")]

        elif name == "disconnect":
            await cdp.disconnect()
            cdp = None
            return [TextContent(type="text", text="Disconnected from browser")]

        return [TextContent(type="text", text=f"Unknown tool: {name}")]

    except Exception as e:
        return [TextContent(
            type="text",
            text=f"Error: {str(e)}"
        )]


async def main():
    server_options = InitializationOptions(
        server_name="helium-browser",
        server_version="0.1.0",
        capabilities={},
    )
    async with stdio_server() as streams:
        await app.run(streams[0], streams[1], server_options)


if __name__ == "__main__":
    asyncio.run(main())
