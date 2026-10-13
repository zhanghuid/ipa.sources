const form = document.querySelector("#app-form");
const list = document.querySelector("#app-list");
const state = document.querySelector("#catalog-state");
const count = document.querySelector("#app-count");
const message = document.querySelector("#form-message");
const submitButton = document.querySelector("#submit-button");
const refreshButton = document.querySelector("#refresh-button");
const formTitle = document.querySelector("#form-title");
const urlFieldLabel = document.querySelector("#url-field-label");
const urlFieldHint = document.querySelector("#url-field-hint");
const urlFieldError = document.querySelector("#url-field-error");
const urlInput = document.querySelector("#ipa-url");
const footerPath = document.querySelector("#footer-path");
const platformTabs = [...document.querySelectorAll(".platform-tab")];
let activeCatalogRequest;
let catalogRequestId = 0;
let platform = "ios";

const PLATFORM_COPY = {
  ios: {
    title: "添加 iOS 应用",
    field: "IPA 下载地址",
    hint: "需为可直接下载的 HTTPS 链接。提交后由 GitHub Actions 下载 IPA 并读取 Bundle ID、版本与图标。",
    placeholder: "https://…/app.ipa",
    path: "config/ios/sources/custom/ipas.json",
    output: "apps.json",
    identifier: "bundleIdentifier",
    expected: "ipa",
    rejected: ["apk", "apks", "xapk"],
    storeHint: "App Store / TestFlight 页面不是 IPA 直链",
  },
  android: {
    title: "添加 Android 应用",
    field: "APK 下载地址",
    hint: "需为可直接下载的 HTTPS 链接。提交后由 GitHub Actions 下载 APK 并读取包名、版本与图标。",
    placeholder: "https://…/app.apk",
    path: "config/android/sources/custom/apks.json",
    output: "android.json",
    identifier: "packageName",
    expected: "apk",
    rejected: ["ipa"],
    storeHint: "Google Play 页面不是 APK 直链",
  },
};

const STORE_HOSTS = [
  ["apps.apple.com", "App Store 应用页面不是可直接下载的 IPA 文件，请填写 IPA 直链"],
  ["itunes.apple.com", "iTunes 页面不是可直接下载的 IPA 文件，请填写 IPA 直链"],
  ["testflight.apple.com", "TestFlight 邀请链接不是可直接下载的 IPA 文件，请填写 IPA 直链"],
  ["play.google.com", "Google Play 页面不是可直接下载的 APK 文件，请填写 APK 直链"],
];

// GitHub 页面地址只会返回 HTML 目录，不会返回安装包本体。
const GITHUB_PAGE_PATHS = [
  [/\/(tree|blob|commits|commit|issues|pull|discussions|wiki|actions)(\/|$)/, "GitHub 页面不是文件直链，请填写文件本身的下载地址"],
  [/^\/[^/]+\/[^/]+\/releases(\/(tag|latest)(\/[^/]+)?)?\/?$/, "GitHub Release 页面不是文件直链，请填写具体资产的下载地址（含 /releases/download/）"],
];

const TEMPORARY_ASSET_HOSTS = [
  "release-assets.githubusercontent.com",
  "objects.githubusercontent.com",
  "github-cloud.s3.amazonaws.com",
];

const TEMPORARY_ASSET_MESSAGE = "这是 GitHub 的临时签名下载链接，过期后会失效；请改用 /releases/download/ 形式的稳定地址";

function fileExtensionOf(pathname) {
  const last = pathname.split("/").filter(Boolean).pop() || "";
  const match = last.match(/\.([A-Za-z0-9]{1,10})$/);
  return match ? match[1].toLowerCase() : "";
}

function isGitHubHost(hostname) {
  return hostname === "github.com" || hostname.endsWith(".github.com")
    || hostname === "githubusercontent.com" || hostname.endsWith(".githubusercontent.com");
}

function hasSignedQuery(url) {
  const params = url.searchParams;
  return params.has("X-Amz-Signature") || params.has("X-Amz-Credential")
    || (params.has("sig") && params.has("se"));
}

function urlProblem(raw, platform) {
  const copy = PLATFORM_COPY[platform];
  let url;
  try {
    url = new URL(String(raw || "").trim());
  } catch {
    return "下载地址无效，请填写完整的 HTTPS 链接";
  }
  if (url.protocol !== "https:") return `${copy.field}必须使用 HTTPS`;
  const hostname = url.hostname.toLowerCase();
  const store = STORE_HOSTS.find(([host]) => hostname === host || hostname.endsWith(`.${host}`));
  if (store) return store[1];
  if (TEMPORARY_ASSET_HOSTS.includes(hostname)) return TEMPORARY_ASSET_MESSAGE;
  if (isGitHubHost(hostname)) {
    for (const [pattern, message] of GITHUB_PAGE_PATHS) {
      if (pattern.test(url.pathname)) return message;
    }
    const rawLike = hostname === "raw.githubusercontent.com" || /\/(raw|resolve)\//.test(url.pathname);
    const githubExtension = fileExtensionOf(url.pathname);
    if (rawLike && !githubExtension) {
      return `这个 GitHub 地址指向目录，不是 ${copy.field.replace(" 下载地址", "")} 文件；请填写直达文件的地址（以 .${copy.expected} 结尾）`;
    }
    if (hasSignedQuery(url)) return TEMPORARY_ASSET_MESSAGE;
  }
  const extension = fileExtensionOf(url.pathname);
  if (copy.rejected.includes(extension)) {
    const other = platform === "android" ? "iOS / IPA" : "Android / APK";
    return `当前是 ${platform === "android" ? "Android" : "iOS"} 平台，但这个地址是 .${extension}（属于 ${other}）`;
  }
  if (platform === "android" && (extension === "apks" || extension === "xapk")) {
    return `暂不支持 .${extension} 分包格式，请填写单独的 APK 直链`;
  }
  return "";
}

function setMessage(text, kind = "error") {
  message.textContent = text;
  message.className = `form-message${kind === "success" ? " success" : ""}`;
  message.hidden = false;
}

function setUrlError(text) {
  urlFieldError.textContent = text || "";
  urlFieldError.hidden = !text;
  urlInput.setAttribute("aria-invalid", text ? "true" : "false");
}

function checkUrlField() {
  const value = urlInput.value.trim();
  // Stay quiet until there is something to judge, so the field does not nag mid-typing.
  if (!value) {
    setUrlError("");
    return "";
  }
  const problem = urlProblem(value, platform);
  setUrlError(problem);
  return problem;
}

function latestVersion(app) {
  return [...(app.versions || [])].sort((a, b) => String(b.date || "").localeCompare(String(a.date || "")))[0] || {};
}

function applyPlatformCopy() {
  const copy = PLATFORM_COPY[platform];
  formTitle.textContent = copy.title;
  urlFieldLabel.innerHTML = `${copy.field} <span class="required">*</span>`;
  urlFieldHint.textContent = copy.hint;
  urlInput.placeholder = copy.placeholder;
  footerPath.textContent = copy.path;
  checkUrlField();
  const label = document.querySelector("[data-copy-label]");
  if (label) label.textContent = `复制 ${copy.output}`;
  document.querySelector("button[data-copy-catalog-url]")?.setAttribute("data-platform", platform);
  for (const tab of platformTabs) {
    const selected = tab.dataset.platform === platform;
    tab.classList.toggle("active", selected);
    tab.setAttribute("aria-selected", String(selected));
  }
}

function renderApps(apps) {
  state.hidden = true;
  list.hidden = false;
  list.replaceChildren();
  count.textContent = String(apps.length).padStart(2, "0");
  const copy = PLATFORM_COPY[platform];
  if (!apps.length) {
    const empty = document.createElement("div");
    empty.className = "empty-state";
    const strong = document.createElement("strong");
    strong.textContent = `还没有手动登记的 ${copy.title.replace("添加 ", "")}`;
    const span = document.createElement("span");
    span.textContent = `提交一条 ${copy.field} 后，应用会显示在这里。`;
    empty.append(strong, span);
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
    const identifier = document.createElement("span");
    identifier.textContent = app[copy.identifier] || "元数据待解析";
    identifier.title = identifier.textContent;
    meta.append(versionLabel, identifier);
    info.append(title, meta);
    if (version.metadataError) {
      const failure = document.createElement("p");
      failure.className = "app-error";
      failure.textContent = `解析失败：${version.metadataError}`;
      failure.title = version.metadataError;
      info.append(failure);
    }
    const link = document.createElement("a");
    link.className = "source-link";
    link.href = version.downloadURL || "#";
    link.target = "_blank";
    link.rel = "noreferrer";
    link.textContent = `${copy.field.slice(0, 3)} ↗`;
    link.setAttribute("aria-label", `打开 ${app.name} 的下载链接`);
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
  const versionCount = (app.versions || []).length;
  const confirmed = await window.appConfirm({
    title: `删除「${label}」？`,
    message: `会从自定义源移除该应用${versionCount > 1 ? `及其 ${versionCount} 个已登记版本` : ""}，下次目录生成后不再出现。`,
    detail: "此操作不会删除上游发布，如需恢复请重新登记。",
    confirmLabel: "删除应用",
    trigger: button,
  });
  if (!confirmed) return;
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
      const response = await fetch(`/api/apps?platform=${encodeURIComponent(platform)}`, {
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
  const problem = checkUrlField();
  if (problem) {
    setMessage(problem);
    urlInput.focus();
    return;
  }
  submitButton.disabled = true;
  submitButton.querySelector(".button-label").textContent = "提交并排入解析…";
  const data = Object.fromEntries(new FormData(form));
  try {
    const response = await fetch("/api/apps", {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify({ ...data, platform }),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || `提交失败 (${response.status})`);
    const summary = `${result.updated ? "已更新" : "已添加"} ${result.app.name}，GitHub Actions 将自动下载并读取版本信息。`;
    setMessage(result.warning ? `${summary}（${result.warning}）` : summary, "success");
    form.reset();
    await loadApps();
  } catch (error) {
    setMessage(error.message);
  } finally {
    submitButton.disabled = false;
    submitButton.querySelector(".button-label").textContent = "添加到自有源";
  }
});

for (const tab of platformTabs) {
  tab.addEventListener("click", () => {
    if (tab.dataset.platform === platform) return;
    platform = tab.dataset.platform;
    message.hidden = true;
    applyPlatformCopy();
    loadApps();
  });
}

urlInput.addEventListener("input", checkUrlField);
urlInput.addEventListener("blur", checkUrlField);
refreshButton.addEventListener("click", loadApps);
window.addEventListener("platform-change", (event) => {
  if (event.detail) {
    platform = event.detail;
    applyPlatformCopy();
    loadApps();
  }
});
applyPlatformCopy();
loadApps();
