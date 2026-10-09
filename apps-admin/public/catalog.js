const list = document.querySelector("#app-list");
const state = document.querySelector("#catalog-state");
const count = document.querySelector("#app-count");
const resultCount = document.querySelector("#result-count");
const search = document.querySelector("#search-apps");
const refreshButton = document.querySelector("#refresh-button");
let allApps = [];

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
  link.textContent = "下载 IPA ↗";
  link.setAttribute("aria-label", `下载 ${appName} v${version.version || "未知"}`);
  return link;
}

function isCustomApp(app) {
  return app.developerName === "Personal source" || String(app.iconURL || "").includes("/config/sources/custom/icons/");
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
        name: app.name,
        bundleIdentifier: app.bundleIdentifier || "",
        downloadURL: latestVersion(app).downloadURL || "",
      }),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || `删除失败 (${response.status})`);
    allApps = allApps.filter((item) => item !== app);
    count.textContent = String(allApps.length).padStart(2, "0");
    renderApps();
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
  const initials = String(app.name || "IPA").slice(0, 2).toUpperCase();
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

function makeVersionRow(version, appName) {
  const row = document.createElement("div");
  row.className = "version-row";
  const detail = document.createElement("div");
  detail.className = "version-detail";
  const heading = document.createElement("div");
  heading.className = "version-heading";
  const versionName = document.createElement("strong");
  versionName.textContent = `v${version.version || "未知"}`;
  const meta = document.createElement("span");
  const build = version.buildVersion ? `Build ${version.buildVersion}` : "";
  const date = version.date || "日期未知";
  meta.textContent = [build, date].filter(Boolean).join(" · ");
  heading.append(versionName, meta);
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

function renderApps() {
  const query = search.value.trim().toLocaleLowerCase();
  const apps = allApps.filter((app) => [app.name, app.bundleIdentifier, app.developerName, app.subtitle]
    .some((value) => String(value || "").toLocaleLowerCase().includes(query)));
  list.replaceChildren();
  list.hidden = false;
  resultCount.textContent = query ? `显示 ${apps.length} / ${allApps.length} 个应用` : "";
  if (!apps.length) {
    const empty = document.createElement("div");
    empty.className = "empty-state full-empty-state";
    const heading = document.createElement("strong");
    heading.textContent = query ? "没有匹配的应用" : "目录中还没有应用";
    const text = document.createElement("span");
    text.textContent = query ? "尝试其他名称、Bundle ID 或开发者。" : "生成 apps.json 后，应用会显示在这里。";
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
    subtitle.textContent = [app.developerName, app.bundleIdentifier].filter(Boolean).join(" · ");
    info.append(title, subtitle);
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
      toggle.textContent = "查看版本";
      const history = document.createElement("div");
      history.className = "version-list";
      versions.forEach((version) => history.append(makeVersionRow(version, app.name || "应用")));
      details.append(toggle, history);
      article.append(details);
    }
    list.append(article);
  }
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
      const response = await fetch("/api/catalog", {
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
    allApps = result.apps || [];
    count.textContent = String(allApps.length).padStart(2, "0");
    state.hidden = true;
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
loadCatalog();
