const colors = new Set(["grey", "blue", "red", "yellow", "green", "pink", "purple", "cyan", "orange"]);

function requireId(value) {
  if (!Number.isSafeInteger(value) || value < 0) throw new Error("Expected a native numeric tab, window, or group ID.");
  return value;
}

function requireTitle(value) {
  if (typeof value !== "string" || !value.trim()) throw new Error("Group title must not be empty.");
  return value;
}

function groupProperties(args) {
  const result = {};
  if (args.title !== undefined) result.title = requireTitle(args.title);
  if (args.color !== undefined) {
    if (!colors.has(args.color)) throw new Error("Invalid group color.");
    result.color = args.color;
  }
  if (args.collapsed !== undefined) {
    if (typeof args.collapsed !== "boolean") throw new Error("collapsed must be a boolean.");
    result.collapsed = args.collapsed;
  }
  return result;
}

function tabInfo(tab) {
  return {
    id: tab.id, title: tab.title || "", url: tab.pendingUrl || tab.url || "",
    window_id: tab.windowId, group_id: tab.groupId, active: tab.active,
    pinned: tab.pinned, index: tab.index,
  };
}

async function groupInfo(api, group) {
  return {
    id: group.id, title: group.title || "", color: group.color,
    collapsed: group.collapsed, window_id: group.windowId,
    tab_ids: (await api.tabs.query({groupId: group.id})).map(tab => tab.id),
  };
}

async function sameWindowTabs(api, ids, windowId) {
  if (!Array.isArray(ids) || !ids.length) throw new Error("At least one tab ID is required.");
  const tabs = await Promise.all([...new Set(ids.map(requireId))].map(id => api.tabs.get(id)));
  windowId ??= tabs[0].windowId;
  if (tabs.some(tab => tab.windowId !== windowId)) throw new Error("Tabs and group must share a window.");
  if (tabs.some(tab => tab.pinned)) throw new Error("Pinned tabs cannot be grouped. Unpin the tab yourself first.");
  return tabs;
}

export function prUrl(value) {
  try {
    const url = new URL(value);
    const match = url.pathname.match(/^\/([^/]+)\/([^/]+)\/pull\/([1-9]\d*)(?:\/|$)/);
    if (url.protocol === "https:" && url.hostname === "github.com" && !url.port && !url.username && !url.password && match) {
      return `https://github.com/${match[1].toLowerCase()}/${match[2].toLowerCase()}/pull/${match[3]}`;
    }
  } catch {}
  return null;
}

async function ensurePrTab(api, args) {
  const url = prUrl(args.url);
  if (!url) throw new Error("Expected an https://github.com/OWNER/REPO/pull/NUMBER URL.");
  const title = requireTitle(args.group_title);
  const filter = args.window_id === undefined ? {} : {windowId: requireId(args.window_id)};
  const tabs = (await api.tabs.query({windowType: "normal"})).filter(tab => prUrl(tab.pendingUrl || tab.url) === url);
  const groups = (await api.tabGroups.query(filter)).filter(group => group.title === title);
  let tab = tabs.find(item => groups.some(group => item.groupId === group.id));
  let group = groups.find(item => item.id === tab?.groupId);
  if (!tab) {
    tab = tabs.find(item => groups.some(candidate => candidate.windowId === item.windowId)) || tabs.find(item => item.windowId === args.window_id) || tabs.find(item => item.active) || tabs[0];
    const sameWindow = groups.filter(item => item.windowId === tab?.windowId);
    if ((sameWindow.length || groups.length) > 1) throw new Error("Several groups have this title. Supply window_id or choose a unique group title.");
    group = sameWindow[0] || groups[0];
  }
  if (tab?.pinned) throw new Error("The existing PR tab is pinned and cannot be grouped. Unpin it yourself first.");
  const createdTab = !tab;
  const createdGroup = !group;
  if (!tab) {
    const windowId = group?.windowId ?? args.window_id;
    tab = await api.tabs.create({url, active: false, ...(windowId === undefined ? {} : {windowId})});
  }
  const targetWindow = group?.windowId ?? args.window_id;
  if (targetWindow !== undefined && tab.windowId !== targetWindow) {
    await api.tabs.move(tab.id, {windowId: targetWindow, index: -1});
    tab = await api.tabs.get(tab.id);
  }
  if (!group || tab.groupId !== group.id) {
    const id = await api.tabs.group({tabIds: [tab.id], ...(group ? {groupId: group.id} : {createProperties: {windowId: tab.windowId}})});
    group = group || await api.tabGroups.update(id, {title});
  }
  tab = await api.tabs.get(tab.id);
  group = await api.tabGroups.get(group.id);
  if (tab.groupId !== group.id || group.title !== title) throw new Error("Could not verify PR tab group membership. Inspect tabs before retrying.");
  return {tab: tabInfo(tab), group: await groupInfo(api, group), created_tab: createdTab, created_group: createdGroup};
}

export async function dispatch(api, method, args = {}) {
  switch (method) {
    case "list_tabs_with_groups":
      return (await api.tabs.query({windowType: "normal"})).map(tabInfo);
    case "list_tab_groups":
      return Promise.all((await api.tabGroups.query({})).map(group => groupInfo(api, group)));
    case "create_tab_group": {
      const properties = groupProperties({...args, title: requireTitle(args.title)});
      const tabs = await sameWindowTabs(api, args.tab_ids);
      const id = await api.tabs.group({tabIds: tabs.map(tab => tab.id), createProperties: {windowId: tabs[0].windowId}});
      return groupInfo(api, await api.tabGroups.update(id, properties));
    }
    case "add_tabs_to_group": {
      const group = await api.tabGroups.get(requireId(args.group_id));
      const tabs = await sameWindowTabs(api, args.tab_ids, group.windowId);
      await api.tabs.group({tabIds: tabs.map(tab => tab.id), groupId: group.id});
      return groupInfo(api, await api.tabGroups.get(group.id));
    }
    case "remove_tabs_from_group": {
      if (!Array.isArray(args.tab_ids) || !args.tab_ids.length) throw new Error("At least one tab ID is required.");
      const ids = args.tab_ids.map(requireId);
      await Promise.all(ids.map(id => api.tabs.get(id)));
      await api.tabs.ungroup(ids);
      return {tab_ids: ids};
    }
    case "update_tab_group":
      return groupInfo(api, await api.tabGroups.update(requireId(args.group_id), groupProperties(args)));
    case "ensure_pr_tab":
      return ensurePrTab(api, args);
    default:
      throw new Error(`Unsupported tab-group method: ${method}`);
  }
}
