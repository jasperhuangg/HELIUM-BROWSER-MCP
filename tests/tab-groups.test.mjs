import assert from "node:assert/strict";
import test from "node:test";
import {dispatch, prUrl} from "../helium_mcp/extension/tab-groups.js";

const pr = "https://github.com/owner/repo/pull/12";

function browser(initialTabs = [], initialGroups = []) {
  const tabs = new Map(initialTabs.map(tab => [tab.id, {groupId: -1, pinned: false, active: false, index: 0, ...tab}]));
  const groups = new Map(initialGroups.map(group => [group.id, {color: "blue", collapsed: false, ...group}]));
  const calls = [];
  let nextTab = 100;
  let nextGroup = 100;
  const copy = value => structuredClone(value);
  const getTab = id => {
    assert.ok(tabs.has(id), `Missing tab ${id}`);
    return tabs.get(id);
  };
  const getGroup = id => {
    assert.ok(groups.has(id), `Missing group ${id}`);
    return groups.get(id);
  };
  function clearEmptyGroups() {
    for (const id of groups.keys()) {
      if (![...tabs.values()].some(tab => tab.groupId === id)) groups.delete(id);
    }
  }
  const api = {
    tabs: {
      query: async query => [...tabs.values()].filter(tab => Object.entries(query).every(([key, value]) => key === "windowType" || tab[key] === value)).map(copy),
      get: async id => copy(getTab(id)),
      create: async args => {
        calls.push(["create", args]);
        const tab = {id: nextTab++, windowId: 1, groupId: -1, pinned: false, index: 0, ...args};
        tabs.set(tab.id, tab);
        return copy(tab);
      },
      move: async (id, args) => {
        calls.push(["move", id, args]);
        Object.assign(getTab(id), args, {groupId: -1});
        clearEmptyGroups();
        return copy(getTab(id));
      },
      group: async args => {
        calls.push(["group", args]);
        const id = args.groupId ?? nextGroup++;
        if (args.groupId === undefined) groups.set(id, {id, windowId: args.createProperties.windowId, title: "", color: "grey", collapsed: false});
        const group = getGroup(id);
        for (const tabId of args.tabIds) {
          assert.equal(getTab(tabId).windowId, group.windowId);
          getTab(tabId).groupId = id;
        }
        clearEmptyGroups();
        return id;
      },
      ungroup: async ids => {
        calls.push(["ungroup", ids]);
        for (const id of ids) getTab(id).groupId = -1;
        clearEmptyGroups();
      },
    },
    tabGroups: {
      query: async query => [...groups.values()].filter(group => Object.entries(query).every(([key, value]) => group[key] === value)).map(copy),
      get: async id => copy(getGroup(id)),
      update: async (id, args) => {
        calls.push(["update", id, args]);
        Object.assign(getGroup(id), args);
        return copy(getGroup(id));
      },
    },
  };
  return {api, tabs, groups, calls};
}

test("PR identity includes owner, repository, and full number but ignores subpages, queries, and fragments", () => {
  assert.equal(prUrl("https://github.com/Owner/Repo/pull/12/changes#diff-abc"), pr);
  assert.equal(prUrl(`${pr}?tab=checks`), pr);
  for (const value of ["https://github.com/owner/repo/pull/123x", "https://github.com/owner/repo/issues/12", "https://evil.test/owner/repo/pull/12", "https://github.com.evil.test/owner/repo/pull/12", "http://github.com/owner/repo/pull/12", "https://github.com/owner/repo/pull/0", "https://user@github.com/owner/repo/pull/12"]) {
    assert.equal(prUrl(value), null);
  }
});

test("missing PR creates one background tab in a named group and repeat calls change nothing", async () => {
  const b = browser();
  const first = await dispatch(b.api, "ensure_pr_tab", {url: pr, group_title: "My task"});
  assert.equal(first.created_tab, true);
  assert.equal(first.created_group, true);
  assert.equal(first.group.title, "My task");
  assert.equal(first.tab.group_id, first.group.id);
  assert.equal(first.tab.active, false);
  const count = b.calls.length;
  const second = await dispatch(b.api, "ensure_pr_tab", {url: `${pr}/files?x=1#diff`, group_title: "My task"});
  assert.equal(second.tab.id, first.tab.id);
  assert.equal(second.created_tab, false);
  assert.equal(second.created_group, false);
  assert.equal(b.calls.length, count);
});

test("an already-open ungrouped PR is grouped instead of duplicated", async () => {
  const b = browser([{id: 1, windowId: 2, url: `${pr}/changes#diff`}]);
  const result = await dispatch(b.api, "ensure_pr_tab", {url: pr, group_title: "Task"});
  assert.equal(result.tab.id, 1);
  assert.equal(result.created_tab, false);
  assert.equal(b.tabs.size, 1);
  assert.equal(result.group.window_id, 2);
});

test("matching a pending navigation avoids opening a second tab", async () => {
  const b = browser([{id: 1, windowId: 1, url: "about:blank", pendingUrl: pr}]);
  const result = await dispatch(b.api, "ensure_pr_tab", {url: pr, group_title: "Task"});
  assert.equal(result.created_tab, false);
});

test("an existing group receives a missing PR in its window without changing group settings", async () => {
  const b = browser([{id: 2, windowId: 2, groupId: 5}], [{id: 5, windowId: 2, title: "Task", collapsed: true, color: "green"}]);
  const result = await dispatch(b.api, "ensure_pr_tab", {url: pr, group_title: "Task"});
  assert.equal(result.tab.window_id, 2);
  assert.equal(result.group.id, 5);
  assert.equal(result.group.collapsed, true);
  assert.equal(result.group.color, "green");
  assert.equal(b.groups.size, 1);
});

test("an existing PR in another window moves into the existing group without touching other tabs", async () => {
  const b = browser([{id: 1, windowId: 1, url: pr}, {id: 2, windowId: 1, url: "https://example.org"}, {id: 3, windowId: 2, groupId: 5}], [{id: 5, windowId: 2, title: "Task"}]);
  const result = await dispatch(b.api, "ensure_pr_tab", {url: pr, group_title: "Task"});
  assert.equal(result.tab.id, 1);
  assert.equal(result.group.id, 5);
  assert.equal(b.tabs.get(1).windowId, 2);
  assert.equal(b.tabs.get(2).windowId, 1);
  assert.equal(b.tabs.size, 3);
});

test("moving a PR out of another group preserves its siblings", async () => {
  const b = browser([{id: 1, windowId: 1, url: pr, groupId: 4}, {id: 2, windowId: 1, groupId: 4}, {id: 3, windowId: 1, groupId: 5}], [{id: 4, windowId: 1, title: "Other"}, {id: 5, windowId: 1, title: "Task"}]);
  await dispatch(b.api, "ensure_pr_tab", {url: pr, group_title: "Task"});
  assert.equal(b.tabs.get(1).groupId, 5);
  assert.equal(b.tabs.get(2).groupId, 4);
  assert.equal(b.groups.get(4).title, "Other");
});

test("an existing correctly-grouped duplicate is preferred without closing other duplicates", async () => {
  const b = browser([{id: 1, windowId: 1, url: pr}, {id: 2, windowId: 2, url: `${pr}/checks`, groupId: 5}], [{id: 5, windowId: 2, title: "Task"}]);
  const result = await dispatch(b.api, "ensure_pr_tab", {url: pr, group_title: "Task"});
  assert.equal(result.tab.id, 2);
  assert.equal(b.calls.length, 0);
  assert.equal(b.tabs.size, 2);
});

test("duplicate group titles require disambiguation before creating a tab", async () => {
  const b = browser([{id: 1, windowId: 1, groupId: 4}, {id: 2, windowId: 2, groupId: 5}], [{id: 4, windowId: 1, title: "Task"}, {id: 5, windowId: 2, title: "Task"}]);
  await assert.rejects(dispatch(b.api, "ensure_pr_tab", {url: pr, group_title: "Task"}), /Several groups/);
  assert.equal(b.calls.length, 0);
  const result = await dispatch(b.api, "ensure_pr_tab", {url: pr, group_title: "Task", window_id: 2});
  assert.equal(result.group.id, 5);
});

test("an existing PR disambiguates duplicate group titles by its window", async () => {
  const b = browser([{id: 1, windowId: 1, groupId: 4}, {id: 2, windowId: 2, url: pr}, {id: 3, windowId: 2, groupId: 5}], [{id: 4, windowId: 1, title: "Task"}, {id: 5, windowId: 2, title: "Task"}]);
  const result = await dispatch(b.api, "ensure_pr_tab", {url: pr, group_title: "Task"});
  assert.equal(result.group.id, 5);
});

test("window_id selects a destination without duplicating a PR in another window", async () => {
  const b = browser([{id: 1, windowId: 1, url: pr}]);
  const result = await dispatch(b.api, "ensure_pr_tab", {url: pr, group_title: "Task", window_id: 2});
  assert.equal(result.tab.id, 1);
  assert.equal(result.created_tab, false);
  assert.equal(result.group.window_id, 2);
  assert.equal(b.tabs.size, 1);
});

test("pinned tabs and invalid requests fail before mutation", async () => {
  const b = browser([{id: 1, windowId: 1, url: pr, pinned: true}, {id: 2, windowId: 2}]);
  await assert.rejects(dispatch(b.api, "ensure_pr_tab", {url: pr, group_title: "Task"}), /pinned/);
  await assert.rejects(dispatch(b.api, "ensure_pr_tab", {url: "https://evil.test", group_title: "Task"}), /Expected/);
  await assert.rejects(dispatch(b.api, "create_tab_group", {tab_ids: [1, 2], title: "Task"}), /share a window/);
  await assert.rejects(dispatch(b.api, "create_tab_group", {tab_ids: [1], title: "Task"}), /Pinned/);
  await assert.rejects(dispatch(b.api, "create_tab_group", {tab_ids: [2], title: "Task", color: "bad"}), /color/);
  await assert.rejects(dispatch(b.api, "create_tab_group", {tab_ids: [], title: "Task"}), /At least/);
  await assert.rejects(dispatch(b.api, "ensure_pr_tab", {url: pr, group_title: "  "}), /empty/);
  assert.equal(b.calls.length, 0);
});

test("native group tools list, create, add, rename, collapse, and ungroup", async () => {
  const b = browser([{id: 1, windowId: 1}, {id: 2, windowId: 1}, {id: 3, windowId: 2}]);
  const created = await dispatch(b.api, "create_tab_group", {tab_ids: [1], title: "Task", color: "purple"});
  const added = await dispatch(b.api, "add_tabs_to_group", {tab_ids: [2], group_id: created.id});
  assert.deepEqual(added.tab_ids, [1, 2]);
  await assert.rejects(dispatch(b.api, "add_tabs_to_group", {tab_ids: [3], group_id: created.id}), /share a window/);
  const updated = await dispatch(b.api, "update_tab_group", {group_id: created.id, title: "Renamed", collapsed: true});
  assert.equal(updated.title, "Renamed");
  assert.equal(updated.collapsed, true);
  assert.equal(updated.color, "purple");
  assert.equal((await dispatch(b.api, "list_tab_groups"))[0].id, created.id);
  assert.equal((await dispatch(b.api, "list_tabs_with_groups"))[0].group_id, created.id);
  await dispatch(b.api, "remove_tabs_from_group", {tab_ids: [1, 2]});
  assert.equal(b.groups.size, 0);
  assert.equal(b.tabs.size, 3);
  await assert.rejects(dispatch(b.api, "unknown"), /Unsupported/);
});
