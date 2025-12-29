const DOI_RE = /\b(10\.\d{4,9}\/[-._;()/:A-Z0-9]+)\b/i;

function findDoi() {
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
}

function createFab() {
  const fab = document.createElement("button");
  fab.id = "doi-helper-fab";
  fab.type = "button";
  fab.textContent = "DOI";
  fab.style.cssText = `
    position:fixed;
    right:14px;
    top:50%;
    transform:translateY(-50%);
    z-index:2147483647;
    border-radius:999px;
    padding:10px 12px;
    border:1px solid rgba(13,34,77,.55);
    background:linear-gradient(180deg,#0b1b3a,#0a1733);
    color:#eaf0ff;
    font:700 12px/1 -apple-system,BlinkMacSystemFont,Segoe UI,Inter,Roboto,Arial,sans-serif;
    box-shadow:0 10px 30px rgba(0,0,0,.35);
    cursor:pointer;
    user-select:none;
  `;
  fab.addEventListener("click", async () => {
    const doi = findDoi();
    chrome.runtime.sendMessage({ type: "fabClick", doi });
  });
  document.documentElement.appendChild(fab);
  return fab;
}

function setFabColor(fab, color) {
  if (!fab) return;
  const map = {
    green: "rgba(43,212,125,.95)",
    yellow: "rgba(255,204,102,.98)",
    red: "rgba(255,77,109,.95)",
    gray: "linear-gradient(180deg,#0b1b3a,#0a1733)",
  };
  const bg = map[color] || map.gray;
  if (bg.startsWith("linear-gradient")) {
    fab.style.background = bg;
    fab.style.color = "#eaf0ff";
  } else {
    fab.style.background = bg;
    fab.style.color = "#071225";
  }
}

async function updateFab(fab) {
  const doi = findDoi();
  if (!doi) {
    fab.title = "No DOI detected";
    setFabColor(fab, "gray");
    return;
  }
  fab.title = `DOI: ${doi}\nClick to send (Open Access only)`;

  const cfgResp = await chrome.runtime.sendMessage({ type: "getConfig" });
  const apiBase = cfgResp?.data?.apiBase;
  if (!apiBase) return;

  try {
    const resp = await fetch(`${apiBase}/api/v1/doi_info`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ doi }),
    });
    const data = await resp.json().catch(() => ({}));
    if (data?.ok && data?.color) setFabColor(fab, data.color);
  } catch {
    // ignore
  }
}

const fab = createFab();
updateFab(fab);
setInterval(() => updateFab(fab), 8000);
