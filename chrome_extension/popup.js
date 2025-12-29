const DEFAULT_API_BASE = "http://127.0.0.1:8787";

const $ = (id) => document.getElementById(id);

function show(id) {
  for (const el of ["viewWelcome", "viewLogin", "viewDash"]) $(el).classList.add("hidden");
  $(id).classList.remove("hidden");
}

function setPill(el, text, cls) {
  el.textContent = text;
  el.classList.remove("ok", "bad", "warn");
  if (cls) el.classList.add(cls);
}

async function sendApi(path, body) {
  const res = await chrome.runtime.sendMessage({ type: "api", path, body });
  if (!res?.ok) throw new Error(res?.error || "API error");
  return res.data;
}

async function getLocal(keys) {
  return await chrome.storage.local.get(keys);
}

async function setLocal(obj) {
  await chrome.storage.local.set(obj);
}

async function getActiveTab() {
  const tabs = await chrome.tabs.query({ active: true, currentWindow: true });
  return tabs?.[0] || null;
}

async function extractDoiFromPage() {
  const tab = await getActiveTab();
  if (!tab?.id) return null;
  const [{ result } = { result: null }] = await chrome.scripting.executeScript({
    target: { tabId: tab.id },
    func: () => {
      const DOI_RE = /\b(10\.\d{4,9}\/[-._;()/:A-Z0-9]+)\b/i;
      const metas = [
        'meta[name="citation_doi"]',
        'meta[name="dc.identifier"]',
        'meta[name="dc.Identifier"]',
        'meta[name="DC.Identifier"]',
      ];
      for (const sel of metas) {
        const el = document.querySelector(sel);
        const content = (el?.getAttribute("content") || "").trim();
        if (!content) continue;
        const m = content.match(DOI_RE);
        if (m?.[1]) return m[1];
      }
      const text = `${document.title}\n${document.body?.innerText || ""}`.slice(0, 200000);
      const m = text.match(DOI_RE);
      return m?.[1] || null;
    },
  });
  return result || null;
}

async function refreshDoi() {
  $("sendError").classList.add("hidden");
  $("doiMsg").textContent = "Detecting DOI…";
  $("doiText").textContent = "—";
  setPill($("doiPill"), "No DOI", "");
  $("btnSend").disabled = true;
  $("btnOpenDoi").disabled = true;

  const doi = await extractDoiFromPage();
  if (!doi) {
    $("doiMsg").textContent = "No DOI detected on this page.";
    return null;
  }

  $("doiText").textContent = doi;
  $("btnOpenDoi").disabled = false;
  $("btnOpenDoi").onclick = () => chrome.tabs.create({ url: `https://doi.org/${encodeURIComponent(doi)}` });

  const { email, code } = await getLocal(["email", "code"]);
  const info = await sendApi("/api/v1/doi_info", { doi });

  if (info.color === "green") setPill($("doiPill"), "Open Access", "ok");
  else if (info.color === "yellow") setPill($("doiPill"), "Unknown (pre‑2022)", "warn");
  else setPill($("doiPill"), "Paywalled", "bad");

  $("doiMsg").textContent =
    info.color === "green"
      ? "Looks Open Access. You can send it to the bot."
      : "Not Open Access. The extension will not send this DOI.";

  $("btnSend").disabled = info.color !== "green" || !email || !code;
  $("btnSend").onclick = async () => {
    $("sendError").classList.add("hidden");
    try {
      await sendApi("/api/v1/submit_doi", { email, code, doi });
      $("doiMsg").textContent = "Queued. Check Telegram.";
    } catch (e) {
      $("sendError").textContent = String(e?.message || e);
      $("sendError").classList.remove("hidden");
    }
  };

  return doi;
}

async function loadDashboard() {
  const { email, code } = await getLocal(["email", "code"]);
  if (!email || !code) {
    $("btnLogout").classList.add("hidden");
    show("viewWelcome");
    return;
  }

  $("btnLogout").classList.remove("hidden");
  show("viewDash");

  try {
    const data = await sendApi("/api/v1/me", { email, code });
    setPill($("accountStatus"), data.account_active ? "Active" : "Inactive", data.account_active ? "ok" : "bad");
    setPill($("aiStatus"), data.ai_active ? "Enabled" : "Disabled", data.ai_active ? "ok" : "bad");
    $("freeUsed").textContent = data.quota.used_free;
    $("freeRemain").textContent = data.quota.remaining_free;
    $("paidUsed").textContent = data.quota.used_paid;
    $("paidRemain").textContent = data.quota.remaining_paid;
  } catch (e) {
    setPill($("accountStatus"), "API error", "bad");
    $("doiMsg").textContent = String(e?.message || e);
  }

  await refreshDoi();
}

async function init() {
  const cfg = await getLocal(["apiBase", "proxyEnabled"]);
  $("apiBase").value = cfg.apiBase || DEFAULT_API_BASE;

  $("btnSaveApi").onclick = async () => {
    const apiBase = ($("apiBase").value || DEFAULT_API_BASE).replace(/\/+$/, "");
    await setLocal({ apiBase });
    await loadDashboard();
  };

  $("proxyToggle").onclick = async () => {
    const cur = await getLocal(["proxyEnabled"]);
    const next = !Boolean(cur.proxyEnabled);
    await setLocal({ proxyEnabled: next });
    $("proxyToggle").setAttribute("aria-pressed", String(next));
    $("proxyToggle").textContent = next ? "On" : "Off";
  };

  $("btnShowLogin").onclick = () => show("viewLogin");
  $("btnBack").onclick = () => show("viewWelcome");

  $("btnLogin").onclick = async () => {
    $("loginError").classList.add("hidden");
    const email = ($("email").value || "").trim();
    const code = ($("code").value || "").trim();
    try {
      await sendApi("/api/v1/login", { email, code });
      await setLocal({ email, code });
      await loadDashboard();
    } catch (e) {
      $("loginError").textContent = "Email or code is incorrect.";
      $("loginError").classList.remove("hidden");
    }
  };

  $("btnLogout").onclick = async () => {
    await setLocal({ email: "", code: "" });
    $("btnLogout").classList.add("hidden");
    show("viewWelcome");
  };

  $("btnRefresh").onclick = refreshDoi;

  const cur = await getLocal(["proxyEnabled"]);
  $("proxyToggle").setAttribute("aria-pressed", String(Boolean(cur.proxyEnabled)));
  $("proxyToggle").textContent = cur.proxyEnabled ? "On" : "Off";

  await loadDashboard();
}

init();
