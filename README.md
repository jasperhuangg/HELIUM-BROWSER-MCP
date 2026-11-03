# Helium MCP Server

MCP (Model Context Protocol) server for Helium browser automation. Control Helium browser (or any Chromium-based browser) programmatically through AI agents.

This fork adds **native tab groups** through a small companion extension. Tab-group tools work with your existing browser session without remote debugging, Accessibility permission, or a browser restart. The original page-automation tools still use CDP.

## Features

- **Navigation**: Navigate to URLs, go back/forward, reload pages
- **Element Interaction**: Click, fill, type (character by character), hover, select dropdowns, focus, scroll, press keys
- **Page Info**: Get HTML, visible text, URL, title, element info/details, viewport size
- **Screenshots**: Capture page screenshots (full page or viewport) as base64 data URIs
- **PDF Export**: Export page as PDF (base64 data URI)
- **Accessibility Tree**: Get accessibility tree for semantic interaction (with fallback)
- **Cookie Management**: Get, set, and delete cookies with full options
- **JavaScript Execution**: Run custom JavaScript in page context
- **Console & Network Logs**: Capture browser console messages and network requests
- **Dialog Handling**: Detect and handle alert/confirm/prompt dialogs
- **Tab Management**: List, create, close, and switch between tabs
- **Native Tab Groups**: List, create, rename, recolor, collapse, add tabs, and ungroup tabs
- **PR Workflow**: `ensure_pr_tab` reuses an open GitHub PR tab and places it in the requested group, opening a tab or creating the group only when needed
- **Wait Operations**: Wait for time, element selector, or page text
- **Persistent CDP Connection**: Single WebSocket connection reused across calls for optimal performance

## Installation

```bash
# Clone the repository
git clone https://github.com/jasperhuangg/HELIUM-BROWSER-MCP.git
cd HELIUM-BROWSER-MCP

# Create virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -e .
```

## Native tab-group setup

1. Start this MCP server in your client (see below).
2. Call `get_tab_group_pairing_info`. It prepares a local extension directory with private pairing details and returns its path.
3. In Helium, open `chrome://extensions`, enable **Developer mode**, select **Load unpacked**, and choose the returned extension directory.
4. The extension pairs automatically. Its options page opens and should show **Connected**. If loading the source extension directory directly instead, enter the returned port and token manually and select **Save and connect**.
5. Call `tab_group_status`; it should report `connected: true`.

Keep the extension directory installed. Browser tab and group IDs are session-scoped; obtain fresh IDs after a browser restart. Native numeric IDs from `list_tabs_with_groups` are different from CDP target IDs returned by `list_tabs`.

### PR workflow

Call `ensure_pr_tab` with:

```json
{
  "url": "https://github.com/owner/repo/pull/123",
  "group_title": "The current Herdr tab title"
}
```

It searches normal browser windows, recognizes PR subpages such as `/changes`, `/files`, and `/checks`, and ignores URL queries and fragments. It prefers an existing tab already in the right group. Otherwise it groups the existing tab, moving that PR tab to the group's window if necessary. If no tab or group exists, it creates what is missing. Existing duplicates are left open, and unrelated tabs and group settings are preserved. It does not explicitly switch focus; moving an active tab between windows can cause the browser to select a replacement tab in the source window.

If several groups have the same title and the PR's window does not disambiguate them, supply `window_id`. A pinned PR tab must be unpinned by the user before it can join a group.

### Local bridge security

The extension requests `tabs`, `tabGroups`, `storage`, and `alarms`, with loopback-only host access. It has no content scripts, cookie permission, or arbitrary JavaScript execution interface. The original CDP tools are separate and retain their existing page-automation capabilities.

The bridge listens only on `127.0.0.1`. HTTP calls require a pairing token; WebSocket connections require an extension origin and that same token. Host checks reject DNS-rebinding requests. Pairing credentials are generated locally in `~/.config/helium-mcp/bridge.json` with file mode `0600`; `HELIUM_MCP_CONFIG_DIR` can override the directory. The prepared extension at `~/.config/helium-mcp/extension` includes a private `pairing.json` for automatic pairing. It is not web-accessible or part of the repository. Treat the token as a secret and do not commit or share the prepared extension directory. Only one browser can pair at a time. Concurrent MCP clients share the authenticated bridge, which serializes requests to avoid duplicate PR tabs and groups.

## Starting a Browser with Remote Debugging

This is needed only for the original CDP tools, **not** for native tab-group tools. A debugging port grants broad access to the browser, including logged-in sessions. Use it only with trusted clients.

```bash
# Helium (Flatpak)
flatpak run com.helium.Browser --remote-debugging-port=9222

# Chrome
google-chrome --remote-debugging-port=9222

# Chromium
chromium-browser --remote-debugging-port=9222

# Brave
brave-browser --remote-debugging-port=9222

# Edge
microsoft-edge --remote-debugging-port=9222
```

## Usage

### Run directly
```bash
python -m helium_mcp.server
```

### Add to your MCP client configuration

**OpenCode V2** (all projects):

```bash
opencode mcp add helium --global -- /absolute/path/to/venv/bin/python -m helium_mcp.server
```

**Claude Desktop** (`~/Library/Application Support/Claude/claude_desktop_config.json`):
```json
{
  "mcpServers": {
    "helium-browser": {
      "command": "python",
      "args": ["-m", "helium_mcp.server"]
    }
  }
}
```

**Cursor** (`.cursor/mcp.json`):
```json
{
  "mcpServers": {
    "helium-browser": {
      "command": "python",
      "args": ["-m", "helium_mcp.server"]
    }
  }
}
```

## Available Tools

### Native tab groups (companion extension; no CDP connection required)

| Tool | Description |
|------|-------------|
| `get_tab_group_pairing_info` | Get local pairing details and extension directory |
| `tab_group_status` | Check the extension connection |
| `list_tabs_with_groups` | List numeric native tab IDs, windows, and group membership |
| `list_tab_groups` | List native groups and their member tab IDs |
| `create_tab_group` | Create a named group from tabs in one window; optional color |
| `add_tabs_to_group` | Add tabs to an existing group in the same window |
| `remove_tabs_from_group` | Ungroup tabs without closing them |
| `update_tab_group` | Rename, recolor, collapse, or expand a group |
| `ensure_pr_tab` | Reuse/open a PR tab and ensure it belongs to the exact named group |

### Connection
| Tool | Description |
|------|-------------|
| `connect` | Connect to browser via CDP (default: localhost:9222) |
| `disconnect` | Disconnect from browser |

### Navigation
| Tool | Description |
|------|-------------|
| `navigate` | Navigate to a URL |
| `reload` | Reload the current page |
| `go_back` | Go back in history |
| `go_forward` | Go forward in history |

### Element Interaction
| Tool | Description |
|------|-------------|
| `click` | Click an element by CSS selector |
| `fill` | Fill an input field (triggers input/change events) |
| `type` | Type text character by character (realistic typing) |
| `hover` | Hover over an element |
| `select` | Select option(s) in a dropdown |
| `focus` | Focus an element |
| `scroll` | Scroll page or element |
| `press_key` | Press a keyboard key (Enter, Tab, Escape, etc.) |

### Page Info
| Tool | Description |
|------|-------------|
| `get_html` | Get current page HTML |
| `get_text` | Get visible page text |
| `get_url` | Get current page URL |
| `get_title` | Get current page title |
| `get_element_info` | Get element attributes, text, position, visibility |
| `screenshot` | Take a screenshot (returns base64 data URI) |
| `get_pdf` | Export page as PDF (returns base64 data URI) |
| `get_viewport` | Get current viewport size |
| `set_viewport` | Set viewport size |
| `get_accessibility_tree` | Get accessibility tree for semantic interaction |

### JavaScript
| Tool | Description |
|------|-------------|
| `evaluate_js` | Execute JavaScript in page context |

### Cookies
| Tool | Description |
|------|-------------|
| `get_cookies` | Get all cookies |
| `set_cookie` | Set a cookie (with domain, path, secure, httpOnly, sameSite) |
| `delete_cookie` | Delete a cookie |
| `clear_cookies` | Clear all cookies |

### Network & Console
| Tool | Description |
|------|-------------|
| `get_console_logs` | Get console log messages |
| `get_network_logs` | Get network request/response logs |

### Tab Management
| Tool | Description |
|------|-------------|
| `list_tabs` | List all open tabs |
| `create_tab` | Create a new tab |
| `close_tab` | Close a tab by ID |
| `switch_tab` | Switch active connection to a different tab |

### Wait & Dialogs
| Tool | Description |
|------|-------------|
| `wait` | Wait for time, selector, or text |
| `handle_dialog` | Accept or dismiss JavaScript dialogs |
| `get_dialog` | Get current pending dialog info |

## Requirements

- Python 3.10+
- Helium browser or any Chromium-based browser (Chrome, Brave, Edge, etc.)
- Companion extension for native tab-group tools (Chromium 120+)
- Browser must be started with `--remote-debugging-port=9222` only for the original CDP tools

## Tests

```bash
python -m unittest discover -s tests -v
node --test tests/tab-groups.test.mjs
```

The Python suite checks bridge authentication, extension pairing, disconnects, and shared clients. The JavaScript suite checks native group operations and the repeatable PR workflow, including existing tabs, duplicate URLs, groups in other windows, and ambiguous group titles.

For an end-to-end test with a disposable profile (your daily browser profile is not touched), install `playwright` in the test environment and run:

```bash
HELIUM_TEST_EXECUTABLE=/Applications/Helium.app/Contents/MacOS/Helium python -m unittest discover -s tests -p test_browser_integration.py -v
```
