import asyncio
import hmac
import json
import os
import re
import secrets
import shutil
import tempfile
from pathlib import Path

import aiohttp
from aiohttp import web
from mcp.types import Tool

BRIDGE_NAME = "helium-tab-groups"
METHODS = {
    "list_tabs_with_groups", "list_tab_groups", "create_tab_group",
    "add_tabs_to_group", "remove_tabs_from_group", "update_tab_group", "ensure_pr_tab",
}
COLORS = ["grey", "blue", "red", "yellow", "green", "pink", "purple", "cyan", "orange"]
TAB_IDS = {"type": "array", "items": {"type": "integer", "minimum": 0}, "minItems": 1}
GROUP_ID = {"type": "integer", "minimum": 0}
COLOR = {"type": "string", "enum": COLORS}


def tool(name, description, properties=None, required=None):
    return Tool(name=name, description=description, inputSchema={
        "type": "object", "properties": properties or {},
        "required": required or [], "additionalProperties": False,
    })


TOOLS = [
    tool("get_tab_group_pairing_info", "Prepare a private, auto-pairing companion extension directory and return its path, port, and token. No CDP connection is needed."),
    tool("tab_group_status", "Check whether the native tab-group companion extension is connected."),
    tool("list_tabs_with_groups", "List native browser tabs with numeric tab IDs, window IDs, and group IDs. These IDs are NOT CDP target IDs."),
    tool("list_tab_groups", "List native tab groups with their titles, colors, windows, and member tab IDs."),
    tool("create_tab_group", "Create a named native group from numeric tab IDs returned by list_tabs_with_groups. Tabs must share a window.", {
        "tab_ids": TAB_IDS, "title": {"type": "string", "minLength": 1}, "color": COLOR,
    }, ["tab_ids", "title"]),
    tool("add_tabs_to_group", "Add numeric tab IDs to an existing native group in the same window.", {
        "tab_ids": TAB_IDS, "group_id": GROUP_ID,
    }, ["tab_ids", "group_id"]),
    tool("remove_tabs_from_group", "Ungroup numeric tab IDs without closing their tabs. Empty groups dissolve.", {
        "tab_ids": TAB_IDS,
    }, ["tab_ids"]),
    tool("update_tab_group", "Rename, recolor, collapse, or expand an existing native group.", {
        "group_id": GROUP_ID, "title": {"type": "string", "minLength": 1},
        "color": COLOR, "collapsed": {"type": "boolean"},
    }, ["group_id"]),
    tool("ensure_pr_tab", "Reuse an open GitHub PR tab (including changes/files/checks subpages), or open one if missing, and ensure it belongs to the exact named group. Reuse an existing group; move only the PR tab between windows if necessary. Does not close duplicates or explicitly switch focus. Safe to repeat.", {
        "url": {"type": "string", "description": "Full https://github.com/OWNER/REPO/pull/NUMBER URL."},
        "group_title": {"type": "string", "minLength": 1},
        "window_id": GROUP_ID,
    }, ["url", "group_title"]),
]
TOOL_NAMES = {entry.name for entry in TOOLS}


def config_directory():
    return Path(os.environ.get("HELIUM_MCP_CONFIG_DIR", Path.home() / ".config" / "helium-mcp"))


def load_settings():
    directory = config_directory()
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = directory / "bridge.json"
    if not path.exists():
        settings = {"port": 37454, "token": secrets.token_urlsafe(32)}
        with tempfile.NamedTemporaryFile(mode="w", dir=directory, delete=False) as handle:
            temporary = Path(handle.name)
            json.dump(settings, handle)
        try:
            # Publish a complete, private file without overwriting another client's token.
            os.link(temporary, path)
        except FileExistsError:
            pass
        finally:
            temporary.unlink()
    settings = json.loads(path.read_text())
    if type(settings.get("port")) is not int or not 1024 <= settings["port"] <= 65535:
        raise ValueError(f"Invalid bridge port in {path}")
    if not isinstance(settings.get("token"), str) or len(settings["token"]) < 32:
        raise ValueError(f"Invalid bridge token in {path}")
    return settings


def prepare_extension(settings):
    directory = config_directory()
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    extension = directory / "extension"
    shutil.copytree(Path(__file__).parent / "extension", extension, dirs_exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", dir=extension, delete=False) as handle:
        temporary = Path(handle.name)
        json.dump(settings, handle)
    temporary.replace(extension / "pairing.json")
    return extension


class ExtensionBroker:
    def __init__(self, token, port, timeout=15):
        self.token = token
        self.port = port
        self.timeout = timeout
        self.socket = None
        self.ready = asyncio.Event()
        self.pending = {}
        self.lock = asyncio.Lock()

    def authorize(self, request):
        if request.host != f"127.0.0.1:{self.port}":
            raise web.HTTPForbidden()
        if request.headers.get("Origin"):
            raise web.HTTPForbidden()
        if not hmac.compare_digest(request.headers.get("Authorization", ""), f"Bearer {self.token}"):
            raise web.HTTPUnauthorized()

    async def status(self, request):
        self.authorize(request)
        return web.json_response({"name": BRIDGE_NAME, "connected": self.ready.is_set()})

    async def call(self, request):
        self.authorize(request)
        try:
            payload = await request.json()
        except (TypeError, ValueError):
            raise web.HTTPBadRequest(text="Invalid JSON")
        if not isinstance(payload, dict) or not isinstance(payload.get("method"), str) or payload["method"] not in METHODS or not isinstance(payload.get("arguments"), dict):
            raise web.HTTPBadRequest(text="Unknown method or invalid arguments")
        async with self.lock:
            try:
                await asyncio.wait_for(self.ready.wait(), timeout=self.timeout)
            except asyncio.TimeoutError:
                return web.json_response({"error": "Companion extension is not connected. Load the extension and pair it using get_tab_group_pairing_info."}, status=503)
            connection = self.socket
            if connection is None:
                return web.json_response({"error": "Extension disconnected before the request was sent. Reconnect and retry."}, status=503)
            request_id = secrets.token_hex(16)
            future = asyncio.get_running_loop().create_future()
            self.pending[request_id] = future
            try:
                await connection.send_json({
                    "type": "request", "id": request_id,
                    "method": payload["method"], "arguments": payload["arguments"],
                })
                result = await asyncio.wait_for(future, timeout=self.timeout)
                return web.json_response(result)
            except (asyncio.TimeoutError, ConnectionError, aiohttp.ClientError):
                return web.json_response({"error": "Extension disconnected or timed out. The operation may have completed; inspect tabs and groups before retrying."}, status=504)
            finally:
                self.pending.pop(request_id, None)

    async def extension(self, request):
        if request.host != f"127.0.0.1:{self.port}" or not re.fullmatch(r"chrome-extension://[a-p]{32}", request.headers.get("Origin", "")):
            raise web.HTTPForbidden()
        socket = web.WebSocketResponse(max_msg_size=1024 * 1024, heartbeat=20)
        await socket.prepare(request)
        try:
            auth = await socket.receive_json(timeout=5)
            token = auth.get("token") if isinstance(auth, dict) else None
            if not isinstance(token, str) or not hmac.compare_digest(token, self.token):
                await socket.close(code=1008, message=b"Invalid pairing token")
                return socket
            if self.socket is not None and not self.socket.closed:
                await socket.close(code=1008, message=b"A browser is already paired")
                return socket
            self.socket = socket
            self.ready.set()
            await socket.send_json({"type": "ready"})
            async for message in socket:
                if message.type != aiohttp.WSMsgType.TEXT:
                    continue
                payload = json.loads(message.data)
                if not isinstance(payload, dict):
                    continue
                future = self.pending.get(payload.get("id"))
                if future is not None and not future.done():
                    future.set_result(payload)
        except (asyncio.TimeoutError, ValueError, TypeError, ConnectionError):
            await socket.close(code=1008, message=b"Invalid extension message")
        finally:
            if self.socket is socket:
                self.socket = None
                self.ready.clear()
                for future in self.pending.values():
                    if not future.done():
                        future.set_exception(ConnectionError("Extension disconnected"))
        return socket

    async def close(self, app):
        if self.socket is not None:
            await self.socket.close()

    def application(self):
        app = web.Application(client_max_size=64 * 1024)
        app.router.add_get("/status", self.status)
        app.router.add_post("/call", self.call)
        app.router.add_get("/extension", self.extension)
        app.on_shutdown.append(self.close)
        return app


class TabGroupBridge:
    def __init__(self, settings=None):
        self.settings = settings
        self.runner = None
        self.start_lock = asyncio.Lock()

    async def __aenter__(self):
        if self.settings is None:
            self.settings = load_settings()
        await self.start()
        return self

    async def __aexit__(self, *args):
        if self.runner is not None:
            await self.runner.cleanup()

    async def start(self):
        async with self.start_lock:
            try:
                await self.request("GET", "/status")
                return
            except aiohttp.ClientConnectionError:
                pass
            if self.runner is not None:
                await self.runner.cleanup()
                self.runner = None
            broker = ExtensionBroker(self.settings["token"], self.settings["port"])
            runner = web.AppRunner(broker.application(), access_log=None)
            await runner.setup()
            try:
                await web.TCPSite(runner, "127.0.0.1", self.settings["port"]).start()
            except OSError:
                await runner.cleanup()
                # Other chats share the authenticated local bridge instead of competing for its port.
                await self.request("GET", "/status")
            else:
                self.runner = runner

    async def request(self, method, path, payload=None):
        url = f"http://127.0.0.1:{self.settings['port']}{path}"
        async with (
            aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=35)) as session,
            session.request(
                method, url, json=payload,
                headers={"Authorization": f"Bearer {self.settings['token']}"},
            ) as response,
        ):
            if response.status in (401, 403):
                raise RuntimeError("The bridge port is occupied by a service with different credentials.")
            result = await response.json()
            if response.status >= 400 or "error" in result:
                raise RuntimeError(result.get("error", "Native tab-group request failed"))
            if path == "/status" and result.get("name") != BRIDGE_NAME:
                raise RuntimeError("The bridge port is occupied by another service.")
            return result

    async def invoke(self, name, arguments):
        await self.start()
        if name == "get_tab_group_pairing_info":
            return {**self.settings, "extension_path": str(prepare_extension(self.settings))}
        if name == "tab_group_status":
            return await self.request("GET", "/status")
        return (await self.request("POST", "/call", {"method": name, "arguments": arguments})).get("result")
