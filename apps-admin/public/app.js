const form = document.querySelector("#app-form");
const list = document.querySelector("#app-list");
const state = document.querySelector("#catalog-state");
const count = document.querySelector("#app-count");
const message = document.querySelector("#form-message");
const submitButton = document.querySelector("#submit-button");
const refreshButton = document.querySelector("#refresh-button");
let activeCatalogRequest;
let catalogRequestId = 0;

function setMessage(text, kind = "error") {
  message.textContent = text;
  message.className = `form-message${kind === "success" ? " success" : ""}`;
  message.hidden = false;
}

function latestVersion(app) {
  return [...(app.versions || [])].sort((a, b) => String(b.date || "").localeCompare(String(a.date || "")))[0] || {};
}

function renderApps(apps) {
  state.hidden = true;
  list.hidden = false;
  list.replaceChildren();
  count.textContent = String(apps.length).padStart(2, "0");
  if (!apps.length) {
    const empty = document.createElement("div");
    empty.className = "empty-state";
    empty.innerHTML = "<strong>还没有手动登记的应用</strong><span>提交一条 IPA 下载地址后，应用会显示在这里。</span>";
    list.append(empty);
    return;
  }
  for (const app of apps) {
    const version = latestVersion(app);
    const row = document.createElement("article");
    row.className = "app-row";
    const icon = document.createElement("div");
    icon.className = "app-icon";
    icon.setAttribute("aria-hidden", "true");
    if (app.iconURL) {
      const image = document.createElement("img");
      image.src = app.iconURL;
      image.alt = "";
      image.loading = "lazy";
      image.onerror = () => { icon.textContent = app.name.slice(0, 2).toUpperCase(); };
      icon.append(image);
    } else icon.textContent = app.name.slice(0, 2).toUpperCase();
    const info = document.createElement("div");
    info.className = "app-info";
    const title = document.createElement("p");
    title.className = "app-name";
    title.textContent = app.name;
    const meta = document.createElement("div");
    meta.className = "app-meta";
    const versionLabel = document.createElement("span");
    versionLabel.className = "app-version";
    versionLabel.textContent = `v${version.version || "—"}`;
    const bundle = document.createElement("span");
    bundle.textContent = app.bundleIdentifier;
    bundle.title = app.bundleIdentifier;
    meta.append(versionLabel, bundle);
    info.append(title, meta);
    const link = document.createElement("a");
    link.className = "source-link";
    link.href = version.downloadURL || "#";
    link.target = "_blank";
    link.rel = "noreferrer";
    link.textContent = "IPA ↗";
    link.setAttribute("aria-label", `打开 ${app.name} 的 IPA 下载链接`);
    const actions = document.createElement("div");
    actions.className = "app-row-actions";
    const deleteButton = document.createElement("button");
    deleteButton.className = "delete-button";
    deleteButton.type = "button";
    deleteButton.textContent = "删除";
    deleteButton.setAttribute("aria-label", `删除 ${app.name}`);
    deleteButton.addEventListener("click", () => deleteApp(app, version, deleteButton));
    actions.append(link, deleteButton);
    row.append(icon, info, actions);
    list.append(row);
  }
}

async function deleteApp(app, version, button) {
  const label = app.name || "这个应用";
  if (!window.confirm(`确定删除「${label}」吗？该应用及其全部已登记版本会从自定义源移除。`)) return;
  button.disabled = true;
  button.textContent = "删除中…";
  try {
    const response = await fetch("/api/apps", {
      method: "DELETE",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify({
        name: app.name,
        bundleIdentifier: app.bundleIdentifier || "",
        downloadURL: version.downloadURL || "",
      }),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || `删除失败 (${response.status})`);
    setMessage(`已从自定义源删除 ${result.deleted}。`, "success");
    await loadApps();
  } catch (error) {
    setMessage(error.message || "删除失败，请刷新列表后重试。");
    button.disabled = false;
    button.textContent = "删除";
  }
}

async function loadApps() {
  activeCatalogRequest?.abort();
  const controller = new AbortController();
  activeCatalogRequest = controller;
  const requestId = ++catalogRequestId;
  state.hidden = false;
  state.className = "catalog-state";
  state.replaceChildren();
  const loader = document.createElement("span");
  loader.className = "loader";
  loader.setAttribute("aria-hidden", "true");
  const label = document.createElement("span");
  label.textContent = "正在读取自定义应用…";
  state.append(loader, label);
  list.hidden = true;
  let timeoutId;
  try {
    const timeout = new Promise((_, reject) => {
      timeoutId = setTimeout(() => {
        controller.abort();
        const error = new Error("读取应用列表超时，请检查网络或 Access 登录状态后重试");
        error.name = "TimeoutError";
        reject(error);
      }, 20_000);
    });
    const request = (async () => {
      const response = await fetch("/api/apps", {
        headers: { Accept: "application/json" },
        signal: controller.signal,
      });
      const contentType = response.headers.get("content-type") || "";
      if (!contentType.includes("application/json")) {
        const error = new Error(response.redirected
          ? "Cloudflare Access 登录已过期，请重新登录后刷新页面。"
          : `目录服务返回了非 JSON 响应 (${response.status})，请稍后重试。`);
        error.status = response.status;
        throw error;
      }
      return { response, result: await response.json() };
    })();
    const { response, result } = await Promise.race([request, timeout]);
    if (!response.ok) {
      const error = new Error(result.error || `读取失败 (${response.status})`);
      error.status = response.status;
      throw error;
    }
    renderApps(result.apps || []);
  } catch (error) {
    if (controller.signal.aborted && error.name === "AbortError" && requestId !== catalogRequestId) return;
    state.className = "catalog-state error";
    state.textContent = error.name === "TimeoutError" || error.name === "AbortError"
      ? `${error.message || "读取应用列表超时"}。`
      : error.status === 401
      ? `${error.message} 登录后刷新页面。`
      : error.status === 503
        ? `${error.message} 部署前需要配置 Access 团队域名和应用 AUD。`
        : `${error.message || "读取应用列表失败"}。可点击右侧刷新按钮重试。`;
    count.textContent = "—";
  } finally {
    clearTimeout(timeoutId);
    if (activeCatalogRequest === controller) activeCatalogRequest = undefined;
  }
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  message.hidden = true;
  submitButton.disabled = true;
  submitButton.querySelector(".button-label").textContent = "提交并排入解析…";
  const data = Object.fromEntries(new FormData(form));
  try {
    const response = await fetch("/api/apps", {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify(data),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || `提交失败 (${response.status})`);
    setMessage(`${result.updated ? "已更新" : "已添加"} ${result.app.name}，GitHub Actions 将自动下载 IPA 并读取版本信息。`, "success");
    form.reset();
    await loadApps();
  } catch (error) {
    setMessage(error.message);
  } finally {
    submitButton.disabled = false;
    submitButton.querySelector(".button-label").textContent = "添加到自有源";
  }
});

refreshButton.addEventListener("click", loadApps);
loadApps();
