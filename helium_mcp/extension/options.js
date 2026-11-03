const port = document.getElementById("port");
const token = document.getElementById("token");
const status = document.getElementById("status");
const saved = await chrome.storage.local.get(["port", "token"]);
port.value = saved.port || 37454;
token.value = saved.token || "";

document.getElementById("pairing").addEventListener("submit", async event => {
  event.preventDefault();
  await chrome.storage.local.set({port: Number(port.value), token: token.value.trim()});
  status.textContent = "Connecting…";
});

async function refresh() {
  try {
    const reply = await chrome.runtime.sendMessage({type: "status"});
    status.textContent = reply?.connected ? "Connected" : "Not connected — check that the MCP server is running and the token matches.";
  } catch {
    status.textContent = "Extension service worker is not available. Reload the extension.";
  }
}
setInterval(refresh, 1000);
refresh();
