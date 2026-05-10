# Helium MCP Server

MCP (Model Context Protocol) server for Helium browser automation. Control Helium browser (or any Chromium-based browser) programmatically through AI agents.

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
- **Wait Operations**: Wait for time, element selector, or page text
- **Persistent CDP Connection**: Single WebSocket connection reused across calls for optimal performance

## Installation

```bash
# Clone the repository
git clone https://github.com/yourusername/helium-mcp.git
cd helium-mcp

# Create virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -e .
```

## Starting a Browser with Remote Debugging

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

**Claude Desktop** (`~/Library/Application Support/Claude/claude_desktop_config.json`):
```json
{
  "mcpServers": {
    "helium-browser": {
      "command": "python",
      "args": ["/path/to/helium-mcp/helium_mcp/server.py"]
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
      "args": ["/path/to/helium-mcp/helium_mcp/server.py"]
    }
  }
}
```

## Available Tools

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
- Browser must be started with `--remote-debugging-port=9222`
