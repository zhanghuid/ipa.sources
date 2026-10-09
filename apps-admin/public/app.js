const form = document.querySelector("#app-form");
const list = document.querySelector("#app-list");
const state = document.querySelector("#catalog-state");
const count = document.querySelector("#app-count");
const message = document.querySelector("#form-message");
const submitButton = document.querySelector("#submit-button");
const refreshButton = document.querySelector("#refresh-button");

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
    row.append(icon, info, link);
    list.append(row);
  }
}

async function loadApps() {
  state.hidden = false;
  state.className = "catalog-state";
  state.innerHTML = '<span class="loader" aria-hidden="true"></span><span>正在读取仓库目录…</span>';
  list.hidden = true;
  try {
    const response = await fetch("/api/apps", { headers: { Accept: "application/json" } });
    const result = await response.json();
    if (!response.ok) {
      const error = new Error(result.error || `读取失败 (${response.status})`);
      error.status = response.status;
      throw error;
    }
    renderApps(result.apps || []);
  } catch (error) {
    state.className = "catalog-state error";
    state.textContent = error.status === 401
      ? `${error.message} 登录后刷新页面。`
      : error.status === 503
        ? `${error.message} 部署前需要配置 Access 团队域名和应用 AUD。`
        : `${error.message}。登录后可刷新重试。`;
    count.textContent = "—";
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
