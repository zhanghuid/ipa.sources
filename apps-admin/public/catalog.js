const list = document.querySelector("#app-list");
const state = document.querySelector("#catalog-state");
const count = document.querySelector("#app-count");
const resultCount = document.querySelector("#result-count");
const search = document.querySelector("#search-apps");
const refreshButton = document.querySelector("#refresh-button");
const platformTabs = [...document.querySelectorAll(".platform-tab")];
const deviceChips = [...document.querySelectorAll(".device-chip")];
let catalogs = { ios: [], android: [] };
let platform = "ios";
let device = "all";

const PLATFORM_META = {
  ios: { output: "apps.json", identifier: "bundleIdentifier", archive: "IPA" },
  android: { output: "android.json", identifier: "packageName", archive: "APK" },
};

const DEVICE_LABELS = { phone: "手机", pad: "平板", tv: "TV", watch: "手表", mac: "Mac", vision: "头显" };

function currentApps() {
  return catalogs[platform] || [];
}

function meta() {
  return PLATFORM_META[platform];
}

function latestVersion(app) {
  return [...(app.versions || [])].sort((a, b) => String(b.date || "").localeCompare(String(a.date || "")))[0] || {};
}

function safeDownloadURL(value) {
  try {
    const url = new URL(value);
    return url.protocol === "https:" ? url.href : "";
  } catch {
    return "";
  }
}

function makeDownload(version, appName) {
  const url = safeDownloadURL(version.downloadURL);
  if (!url) return null;
  const link = document.createElement("a");
  link.className = "source-link version-download";
  link.href = url;
  link.target = "_blank";
  link.rel = "noopener noreferrer";
  link.textContent = `下载 ${meta().archive} ↗`;
  link.setAttribute("aria-label", `下载 ${appName} v${version.version || "未知"}`);
  return link;
}

function devicesOf(app) {
  const devices = (app.devices || []).filter(Boolean);
  return devices.length ? devices : ["phone", "pad"];
}

function isCustomApp(app) {
  return app.developerName === "Personal source"
    || String(app.iconURL || "").includes("/sources/custom/icons/");
}

async function deleteCustomApp(app, button) {
  const name = app.name || "这个应用";
  if (!window.confirm(`确定删除「${name}」吗？该应用及其全部已登记版本会从自定义源移除。`)) return;
  button.disabled = true;
  button.textContent = "删除中…";
  try {
    const response = await fetch("/api/apps", {
      method: "DELETE",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify({
        platform,
        name: app.name,
        bundleIdentifier: app.bundleIdentifier || "",
        packageName: app.packageName || "",
        downloadURL: latestVersion(app).downloadURL || "",
      }),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || `删除失败 (${response.status})`);
    catalogs[platform] = currentApps().filter((item) => item !== app);
    updateCounts();
    renderApps();
    state.hidden = true;
    resultCount.textContent = `已从自定义源删除 ${result.deleted}；目录生成文件会在自动同步后更新。`;
  } catch (error) {
    state.hidden = false;
    state.className = "catalog-state error";
    state.textContent = error.message || "删除失败，请刷新目录后重试。";
    button.disabled = false;
    button.textContent = "删除";
  }
}

function makeIcon(app) {
  const icon = document.createElement("div");
  icon.className = "app-icon catalog-app-icon";
  icon.setAttribute("aria-hidden", "true");
  const initials = String(app.name || "APP").slice(0, 2).toUpperCase();
  if (safeDownloadURL(app.iconURL)) {
    const image = document.createElement("img");
    image.src = app.iconURL;
    image.alt = "";
    image.loading = "lazy";
    image.onerror = () => { icon.textContent = initials; };
    icon.append(image);
  } else icon.textContent = initials;
  return icon;
}

function makeDeviceTags(app) {
  const wrapper = document.createElement("div");
  wrapper.className = "device-tags";
  for (const value of devicesOf(app)) {
    const tag = document.createElement("span");
    tag.className = `device-tag device-${value}`;
    tag.textContent = DEVICE_LABELS[value] || value;
    wrapper.append(tag);
  }
  return wrapper;
}

function makeVersionRow(version, appName) {
  const row = document.createElement("div");
  row.className = "version-row";
  const detail = document.createElement("div");
  detail.className = "version-detail";
  const heading = document.createElement("div");
  heading.className = "version-heading";
  const versionName = document.createElement("strong");
  versionName.textContent = `v${version.version || "未知"}`;
  const metaLine = document.createElement("span");
  const parts = [];
  if (version.buildVersion) parts.push(platform === "android" ? `versionCode ${version.buildVersion}` : `Build ${version.buildVersion}`);
  if (version.date) parts.push(version.date);
  if (version.size) parts.push(formatSize(version.size));
  metaLine.textContent = parts.join(" · ");
  heading.append(versionName, metaLine);
  detail.append(heading);
  row.append(detail);
  const description = version.localizedDescription || version.versionDescription;
  if (description) {
    const note = document.createElement("p");
    note.className = "version-description";
    note.textContent = description;
    row.append(note);
  }
  const download = makeDownload(version, appName);
  if (download) row.append(download);
  return row;
}

function formatSize(bytes) {
  const value = Number(bytes);
  if (!Number.isFinite(value) || value <= 0) return "";
  const units = ["B", "KiB", "MiB", "GiB"];
  let index = 0;
  let size = value;
  while (size >= 1024 && index < units.length - 1) {
    size /= 1024;
    index += 1;
  }
  return `${size.toFixed(size >= 100 || index === 0 ? 0 : 1)} ${units[index]}`;
}

function matchesQuery(app, query) {
  if (!query) return true;
  return [app.name, app[meta().identifier], app.developerName, app.subtitle]
    .some((value) => String(value || "").toLocaleLowerCase().includes(query));
}

function renderApps() {
  const query = search.value.trim().toLocaleLowerCase();
  const apps = currentApps().filter((app) =>
    matchesQuery(app, query) && (device === "all" || devicesOf(app).includes(device)));
  list.replaceChildren();
  list.hidden = false;
  const total = currentApps().length;
  const filters = [query ? "搜索" : "", device !== "all" ? `${DEVICE_LABELS[device] || device} 端` : ""].filter(Boolean);
  resultCount.textContent = filters.length ? `显示 ${apps.length} / ${total} 个应用（${filters.join(" + ")}）` : "";
  if (!apps.length) {
    const empty = document.createElement("div");
    empty.className = "empty-state full-empty-state";
    const heading = document.createElement("strong");
    heading.textContent = total ? "没有匹配的应用" : "该平台目录还没有应用";
    const text = document.createElement("span");
    text.textContent = total
      ? "尝试其他关键词，或切换端筛选。"
      : `生成 ${meta().output} 后，应用会显示在这里。`;
    empty.append(heading, text);
    list.append(empty);
    return;
  }
  for (const app of apps) {
    const article = document.createElement("article");
    article.className = "full-app-row";
    article.append(makeIcon(app));
    const info = document.createElement("div");
    info.className = "full-app-info";
    const title = document.createElement("h2");
    title.className = "app-name";
    title.textContent = app.name || "未命名应用";
    const subtitle = document.createElement("p");
    subtitle.className = "full-app-subtitle";
    subtitle.textContent = [app.developerName, app[meta().identifier]].filter(Boolean).join(" · ");
    info.append(title, subtitle, makeDeviceTags(app));
    const versions = [...(app.versions || [])].sort((a, b) => String(b.date || "").localeCompare(String(a.date || "")));
    const latest = latestVersion(app);
    const summary = document.createElement("div");
    summary.className = "full-app-latest";
    const latestLabel = document.createElement("span");
    latestLabel.className = "app-version";
    latestLabel.textContent = `v${latest.version || "—"}`;
    const versionCount = document.createElement("span");
    versionCount.textContent = `${versions.length} 个版本`;
    summary.append(latestLabel, versionCount);
    const actions = document.createElement("div");
    actions.className = "full-app-actions";
    const latestDownload = makeDownload(latest, app.name || "应用");
    if (latestDownload) actions.append(latestDownload);
    if (isCustomApp(app)) {
      const deleteButton = document.createElement("button");
      deleteButton.className = "delete-button";
      deleteButton.type = "button";
      deleteButton.textContent = "删除";
      deleteButton.setAttribute("aria-label", `删除 ${app.name || "自定义应用"}`);
      deleteButton.addEventListener("click", () => deleteCustomApp(app, deleteButton));
      actions.append(deleteButton);
    }
    article.append(info, summary, actions);
    if (versions.length > 1) {
      const details = document.createElement("details");
      details.className = "version-history";
      const toggle = document.createElement("summary");
      toggle.textContent = `查看版本（${versions.length}）`;
      const history = document.createElement("div");
      history.className = "version-list";
      versions.forEach((version) => history.append(makeVersionRow(version, app.name || "应用")));
      details.append(toggle, history);
      article.append(details);
    }
    list.append(article);
  }
}

function updateCounts() {
  for (const tab of platformTabs) {
    const target = tab.querySelector("[data-count-for]") || tab.querySelector(".platform-note");
    if (target) target.textContent = String((catalogs[tab.dataset.platform] || []).length);
  }
  count.textContent = String(currentApps().length).padStart(2, "0");
}

function applyPlatform() {
  for (const tab of platformTabs) {
    const selected = tab.dataset.platform === platform;
    tab.classList.toggle("active", selected);
    tab.setAttribute("aria-selected", String(selected));
  }
  const label = document.querySelector("[data-copy-label]");
  if (label) label.textContent = `复制 ${meta().output}`;
  updateCounts();
}

async function loadCatalog() {
  state.hidden = false;
  state.className = "catalog-state";
  state.replaceChildren();
  const loader = document.createElement("span");
  loader.className = "loader";
  loader.setAttribute("aria-hidden", "true");
  const label = document.createElement("span");
  label.textContent = "正在读取生成目录…";
  state.append(loader, label);
  list.hidden = true;
  resultCount.textContent = "";
  try {
    const controller = new AbortController();
    let timeoutId;
    const timeout = new Promise((_, reject) => {
      timeoutId = setTimeout(() => {
        controller.abort();
        const error = new Error("读取目录超时");
        error.name = "TimeoutError";
        reject(error);
      }, 25_000);
    });
    const request = (async () => {
      const response = await fetch("/api/catalog?platform=all", {
        headers: { Accept: "application/json" },
        signal: controller.signal,
      });
      const contentType = response.headers.get("content-type") || "";
      if (!contentType.includes("application/json")) {
        throw new Error(response.redirected
          ? "Cloudflare Access 登录已过期，请重新登录后刷新目录。"
          : `目录服务返回了非 JSON 响应 (${response.status})，请稍后重试。`);
      }
      return { response, result: await response.json() };
    })();
    const { response, result } = await Promise.race([request, timeout]).finally(() => clearTimeout(timeoutId));
    if (!response.ok) throw new Error(result.error || `读取失败 (${response.status})`);
    catalogs = {
      ios: result.platforms?.ios?.apps || [],
      android: result.platforms?.android?.apps || [],
    };
    state.hidden = true;
    applyPlatform();
    renderApps();
  } catch (error) {
    state.className = "catalog-state error";
    state.textContent = error.name === "TimeoutError" || error.name === "AbortError"
      ? "读取目录超时，请检查网络或 Access 登录状态后重试。"
      : `${error.message || "读取目录失败"} 可点击右侧刷新按钮重试。`;
    count.textContent = "—";
  }
}

search.addEventListener("input", renderApps);
refreshButton.addEventListener("click", loadCatalog);
for (const tab of platformTabs) {
  tab.addEventListener("click", () => {
    if (tab.dataset.platform === platform) return;
    platform = tab.dataset.platform;
    applyPlatform();
    renderApps();
  });
}
for (const chip of deviceChips) {
  chip.addEventListener("click", () => {
    device = chip.dataset.device;
    for (const item of deviceChips) {
      const selected = item === chip;
      item.classList.toggle("active", selected);
      item.setAttribute("aria-pressed", String(selected));
    }
    renderApps();
  });
}
loadCatalog();
