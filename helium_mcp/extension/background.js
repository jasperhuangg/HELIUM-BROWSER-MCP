import {dispatch} from "./tab-groups.js";

let socket = null;
let connected = false;
let queue = Promise.resolve();

async function pairingSettings() {
  const saved = await chrome.storage.local.get(["port", "token"]);
  if (saved.token) return saved;
  try {
    const response = await fetch(chrome.runtime.getURL("pairing.json"));
    if (!response.ok) return {};
    const settings = await response.json();
    await chrome.storage.local.set({port: settings.port, token: settings.token});
    return settings;
  } catch {
    return {};
  }
}

async function connect() {
  if (socket) return;
  const {port, token} = await pairingSettings();
  if (!Number.isInteger(port) || port < 1024 || port > 65535 || typeof token !== "string" || token.length < 32 || socket) return;
  const current = new WebSocket(`ws://127.0.0.1:${port}/extension`);
  socket = current;
  let keepalive;
  current.onopen = () => {
    current.send(JSON.stringify({type: "auth", token}));
    keepalive = setInterval(() => {
      if (current.readyState === WebSocket.OPEN) current.send(JSON.stringify({type: "ping"}));
    }, 20000);
  };
  current.onmessage = event => {
    if (socket !== current) return;
    let message;
    try { message = JSON.parse(event.data); } catch { return; }
    if (message.type === "ready") {
      connected = true;
      return;
    }
    if (message.type !== "request" || typeof message.id !== "string") return;
    queue = queue.then(async () => {
      let reply;
      try {
        reply = {id: message.id, result: await dispatch(chrome, message.method, message.arguments)};
      } catch (error) {
        reply = {id: message.id, error: error.message};
      }
      if (current.readyState === WebSocket.OPEN) current.send(JSON.stringify(reply));
    }).catch(() => {});
  };
  current.onclose = () => {
    clearInterval(keepalive);
    if (socket !== current) return;
    socket = null;
    connected = false;
    setTimeout(connect, 2000);
  };
  current.onerror = () => current.close();
}

chrome.alarms.create("reconnect", {periodInMinutes: 0.5});
chrome.alarms.onAlarm.addListener(() => connect());
chrome.runtime.onStartup.addListener(() => connect());
chrome.runtime.onInstalled.addListener(({reason}) => {
  if (reason === "install") chrome.runtime.openOptionsPage();
  connect();
});
chrome.action.onClicked.addListener(() => chrome.runtime.openOptionsPage());
chrome.storage.onChanged.addListener((changes, area) => {
  if (area !== "local" || (!changes.port && !changes.token)) return;
  const previous = socket;
  socket = null;
  connected = false;
  previous?.close();
  connect();
});
chrome.runtime.onMessage.addListener((message, sender, reply) => {
  if (sender.id === chrome.runtime.id && message.type === "status") reply({connected});
});
connect();
