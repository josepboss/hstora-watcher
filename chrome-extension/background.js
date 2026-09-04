const DEFAULTS = {backendUrl: "https://hstora.itspanel.com", backendUsername: "", backendPassword: "", extensionSecret: ""};

async function settings() {
  return {...DEFAULTS, ...await chrome.storage.local.get(DEFAULTS)};
}

async function report(payload) {
  const config = await settings();
  if (!config.extensionSecret) throw new Error("Set the extension secret in extension settings");
  const response = await fetch(`${config.backendUrl.replace(/\/$/, "")}/api/z2u/offers`, {
    method: "POST",
    headers: requestHeaders(config),
    body: JSON.stringify(payload)
  });
  if (!response.ok) throw new Error(`Backend status update failed (${response.status})`);
  return response.json();
}

async function backend(path, body = {}) {
  const config = await settings();
  if (!config.extensionSecret) return null;
  const response = await fetch(`${config.backendUrl.replace(/\/$/, "")}${path}`, {
    method: "POST",
    headers: requestHeaders(config),
    body: JSON.stringify(body)
  });
  if (!response.ok) throw new Error(`Backend request failed (${response.status})`);
  return response.json();
}

function requestHeaders(config) {
  const headers = {"Content-Type": "application/json", "X-Extension-Secret": config.extensionSecret};
  if (config.backendUsername || config.backendPassword) {
    const bytes = new TextEncoder().encode(`${config.backendUsername}:${config.backendPassword}`);
    let binary = "";
    bytes.forEach(byte => binary += String.fromCharCode(byte));
    headers.Authorization = `Basic ${btoa(binary)}`;
  }
  return headers;
}

chrome.runtime.onInstalled.addListener(() => { chrome.alarms.create("hstora-stock-sync", {periodInMinutes: 1}); pollStockActions(); });
chrome.runtime.onStartup.addListener(() => { chrome.alarms.create("hstora-stock-sync", {periodInMinutes: 1}); pollStockActions(); });
chrome.alarms.onAlarm.addListener(alarm => { if (alarm.name === "hstora-stock-sync") pollStockActions(); });

async function pollStockActions() {
  let action;
  try {
    const response = await backend("/api/z2u/actions/next");
    action = response?.action;
    if (!action) return;
    await runStockAction(action);
    await backend(`/api/z2u/actions/${action.id}`, {success: true});
  } catch (error) {
    if (action) await backend(`/api/z2u/actions/${action.id}`, {success: false, error: error.message}).catch(() => {});
  }
}

async function runStockAction(action) {
  let tabId;
  try {
    const tab = await chrome.tabs.create({url: action.manage_url, active: false});
    tabId = tab.id;
    await waitForTabComplete(tabId);
    const loaded = await chrome.tabs.get(tabId);
    const url = new URL(loaded.url || "https://www.z2u.com/");
    if (!url.pathname.startsWith("/sell/manage")) throw new Error("Z2U is not signed in in this Chrome profile");
    const response = await sendWithRetry(tabId, {type: "Z2U_RUN_ACTION", offerIds: [action.offer_id], action: action.action});
    if (!response?.ok) throw new Error(response?.error || "Z2U did not confirm the stock action");
  } finally {
    if (tabId) await chrome.tabs.remove(tabId).catch(() => {});
  }
}

function waitForTabComplete(tabId) {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => finish(new Error("Z2U page took too long to load")), 30000);
    const listener = (id, info) => { if (id === tabId && info.status === "complete") finish(); };
    const finish = error => { clearTimeout(timer); chrome.tabs.onUpdated.removeListener(listener); error ? reject(error) : resolve(); };
    chrome.tabs.onUpdated.addListener(listener);
    chrome.tabs.get(tabId).then(tab => tab.status === "complete" && finish()).catch(finish);
  });
}

async function sendWithRetry(tabId, message) {
  let lastError;
  for (let attempt = 0; attempt < 10; attempt++) {
    try { return await chrome.tabs.sendMessage(tabId, message); }
    catch (error) { lastError = error; await new Promise(resolve => setTimeout(resolve, 500)); }
  }
  throw lastError || new Error("Could not connect to the Z2U page");
}

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  (async () => {
    if (message.type === "START_LISTING") {
      const config = await settings();
      if (!config.extensionSecret) throw new Error("Open the extension and set its backend secret first");
      const job = {...message.listing, status: "filling", sourceTabId: sender.tab?.id, startedAt: Date.now()};
      await chrome.storage.local.set({pendingListing: job});
      await report({productId: job.productId, status: "filling", listedPrice: job.price});
      await chrome.tabs.create({url: "https://www.z2u.com/sell/create", active: true});
      return {ok: true};
    }
    if (message.type === "GET_PENDING") return {ok: true, job: (await chrome.storage.local.get("pendingListing")).pendingListing || null};
    if (message.type === "REPORT_STATUS") {
      const current = (await chrome.storage.local.get("pendingListing")).pendingListing;
      if (current) await chrome.storage.local.set({pendingListing: {...current, ...message.payload}});
      await report(message.payload);
      if (current?.sourceTabId) chrome.tabs.sendMessage(current.sourceTabId, {type: "STATUS", ...message.payload}).catch(() => {});
      return {ok: true};
    }
    if (message.type === "POLL_STOCK_ACTIONS") {
      await pollStockActions();
      return {ok: true};
    }
    throw new Error("Unknown extension message");
  })().then(sendResponse).catch(error => sendResponse({ok: false, error: error.message}));
  return true;
});
