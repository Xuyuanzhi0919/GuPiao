const universeForm = document.querySelector("#universeForm");
const universeCode = document.querySelector("#universeCode");
const universeListView = document.querySelector("#universeListView");
const sectorForm = document.querySelector("#sectorForm");
const sectorCodeInput = document.querySelector("#sectorCodeInput");
const sectorConfigList = document.querySelector("#sectorConfigList");
const configForm = document.querySelector("#configForm");
const resetConfigButton = document.querySelector("#resetConfigButton");
const configChangeList = document.querySelector("#configChangeList");

async function loadUniverse() {
  const response = await fetch("/api/universe");
  const payload = await response.json();
  const universe = payload.universe || {};
  universeListView.innerHTML = [
    universeGroup("关注池", "include", universe.include || []),
    universeGroup("排除池", "exclude", universe.exclude || []),
  ].join("");
}

function universeGroup(title, listName, codes) {
  const chips = codes.length
    ? codes.map((code) => `<span class="code-chip">${code}<button type="button" data-list="${listName}" data-code="${code}">x</button></span>`).join("")
    : `<div class="muted-line">空</div>`;
  return `<div class="universe-group"><div class="universe-title">${title}</div>${chips}</div>`;
}

async function loadSectors() {
  const response = await fetch("/api/sectors");
  const payload = await response.json();
  const entries = Object.entries(payload.sectors || {}).sort((a, b) => a[0].localeCompare(b[0], "zh-CN"));
  sectorConfigList.innerHTML = entries.length
    ? entries.map(([sector, codes]) => {
        const chips = codes.map((code) => `<span class="code-chip">${code}<button type="button" data-sector="${sector}" data-code="${code}">x</button></span>`).join("");
        return `<div class="sector-config-group"><div class="sector-config-title">${sector}</div>${chips}</div>`;
      }).join("")
    : `<div class="muted-line">空</div>`;
}

async function loadConfig() {
  const response = await fetch("/api/snapshot");
  const payload = await response.json();
  const config = payload.config || {};
  for (const element of configForm.elements) {
    if (!element.name || config[element.name] === undefined) continue;
    element.value = config[element.name];
  }
}

async function loadConfigChanges() {
  const response = await fetch("/api/config/changes?limit=20");
  const payload = await response.json();
  const changes = payload.changes || [];
  if (!changes.length) {
    configChangeList.innerHTML = `<div class="empty">暂无参数变更</div>`;
    return;
  }
  configChangeList.innerHTML = changes
    .map((item) => {
      const time = new Date(item.ts * 1000).toLocaleString("zh-CN", { hour12: false });
      const parts = Object.entries(item.changes || {})
        .map(([key, change]) => `${key}: ${change.before} -> ${change.after}`)
        .join(" / ");
      return `<div class="history-item">
        <span class="tag B">${item.action === "reset" ? "重" : "改"}</span>
        <div>
          <strong>${time}</strong>
          <span>${parts}</span>
        </div>
      </div>`;
    })
    .join("");
}

universeForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const params = new URLSearchParams(new FormData(universeForm));
  const response = await fetch(`/api/universe/add?${params.toString()}`);
  const payload = await response.json();
  universeCode.value = "";
  const universe = payload.universe || {};
  universeListView.innerHTML = [universeGroup("关注池", "include", universe.include || []), universeGroup("排除池", "exclude", universe.exclude || [])].join("");
});

universeListView.addEventListener("click", async (event) => {
  const button = event.target.closest("button[data-list][data-code]");
  if (!button) return;
  const params = new URLSearchParams({ list: button.dataset.list, code: button.dataset.code });
  await fetch(`/api/universe/remove?${params.toString()}`);
  loadUniverse();
});

sectorForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const params = new URLSearchParams(new FormData(sectorForm));
  await fetch(`/api/sectors/add?${params.toString()}`);
  sectorCodeInput.value = "";
  loadSectors();
});

sectorConfigList.addEventListener("click", async (event) => {
  const button = event.target.closest("button[data-sector][data-code]");
  if (!button) return;
  const params = new URLSearchParams({ sector: button.dataset.sector, code: button.dataset.code });
  await fetch(`/api/sectors/remove?${params.toString()}`);
  loadSectors();
});

configForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  for (const element of configForm.elements) {
    if (element instanceof HTMLInputElement && !element.checkValidity()) {
      element.reportValidity();
      return;
    }
  }
  const params = new URLSearchParams(new FormData(configForm));
  const response = await fetch(`/api/config/update?${params.toString()}`);
  const payload = await response.json();
  const config = payload.config || {};
  for (const element of configForm.elements) {
    if (!element.name || config[element.name] === undefined) continue;
    element.value = config[element.name];
  }
  loadConfigChanges();
});

resetConfigButton.addEventListener("click", async () => {
  await fetch("/api/config/reset");
  loadConfig();
  loadConfigChanges();
});

loadUniverse();
loadSectors();
loadConfig();
loadConfigChanges();
