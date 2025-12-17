const DEFAULT_API_BASE = "http://127.0.0.1:8787";

async function getConfig() {
  const cfg = await chrome.storage.local.get(["apiBase", "email", "code", "proxyEnabled"]);
  return {
    apiBase: (cfg.apiBase || DEFAULT_API_BASE).replace(/\/+$/, ""),
    email: cfg.email || "",
    code: cfg.code || "",
    proxyEnabled: Boolean(cfg.proxyEnabled),
  };
}

async function apiRequest(path, body) {
  const cfg = await getConfig();
  const url = `${cfg.apiBase}${path}`;
  const resp = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) {
    const msg = data?.error || data?.message || `HTTP ${resp.status}`;
    throw new Error(msg);
  }
  return data;
}

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  (async () => {
    if (msg?.type === "api") {
      const data = await apiRequest(msg.path, msg.body || {});
      sendResponse({ ok: true, data });
      return;
    }
    if (msg?.type === "getConfig") {
      sendResponse({ ok: true, data: await getConfig() });
      return;
    }
    if (msg?.type === "fabClick") {
      const cfg = await getConfig();
      const doi = (msg.doi || "").trim();
      if (!doi) {
        sendResponse({ ok: false, error: "no_doi" });
        return;
      }
      if (!cfg.email || !cfg.code) {
        sendResponse({ ok: false, error: "not_logged_in" });
        return;
      }
      const info = await apiRequest("/api/v1/doi_info", { doi });
      if (!info?.ok || info?.color !== "green") {
        sendResponse({ ok: false, error: "not_open_access", data: info });
        return;
      }
      const out = await apiRequest("/api/v1/submit_doi", { email: cfg.email, code: cfg.code, doi });
      sendResponse({ ok: true, data: out });
      return;
    }
    sendResponse({ ok: false, error: "unknown_message" });
  })().catch((err) => {
    sendResponse({ ok: false, error: String(err?.message || err) });
  });
  return true;
});
