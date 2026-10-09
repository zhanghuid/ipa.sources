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
  link.className = "source-link";
  link.href = url;
  link.target = "_blank";
  link.rel = "noopener noreferrer";
  link.textContent = "下载 IPA ↗";
  link.setAttribute("aria-label", `下载 ${appName} v${version.version || "未知"}`);
  return link;
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
  const versionName = document.createElement("strong");
  versionName.textContent = `v${version.version || "未知"}`;
  const meta = document.createElement("span");
  const build = version.buildVersion ? `Build ${version.buildVersion}` : "";
  const date = version.date || "日期未知";
  meta.textContent = [build, date].filter(Boolean).join(" · ");
  detail.append(versionName, meta);
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
    article.append(info, summary);
    const latestDownload = makeDownload(latest, app.name || "应用");
    if (latestDownload) article.append(latestDownload);
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
