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
const nameInput = document.querySelector("#app-name");
const entryList = document.querySelector("#entry-list");
const addEntryButton = document.querySelector("#add-entry-button");
const cancelEditButton = document.querySelector("#cancel-edit-button");
const footerPath = document.querySelector("#footer-path");
const platformTabs = [...document.querySelectorAll(".platform-tab")];
let activeCatalogRequest;
let catalogRequestId = 0;
// Name of the entry being edited, or "" while adding a new one.
let editingName = "";
const platformIds = platformTabs.map((tab) => tab.dataset.platform);
// Reloading keeps the tab you were last on instead of jumping back to iOS.
let platform = window.viewPreference?.read("platform", platformIds, "ios") || "ios";

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
}

function setUrlInvalid(input, invalid) {
  if (invalid) input.setAttribute("aria-invalid", "true");
  else input.removeAttribute("aria-invalid");
}

const DEVICE_LABELS = { phone: "手机", pad: "平板", tv: "TV", watch: "手表", mac: "Mac", vision: "头显" };
const DEVICE_IDS = Object.keys(DEVICE_LABELS);

/** Build one address row: an HTTPS input plus the device classes it serves. */
function makeEntryRow(entry = {}) {
  const row = document.createElement("div");
  row.className = "entry-row";

  const head = document.createElement("div");
  head.className = "entry-head";
  const input = document.createElement("input");
  input.type = "url";
  input.inputMode = "url";
  input.className = "entry-url";
  input.placeholder = PLATFORM_COPY[platform].placeholder;
  input.value = entry.downloadURL || "";
  input.setAttribute("aria-label", `${PLATFORM_COPY[platform].field} 下载地址`);
  const remove = document.createElement("button");
  remove.type = "button";
  remove.className = "icon-button entry-remove";
  remove.innerHTML = '<svg aria-hidden="true" viewBox="0 0 20 20"><path d="M5.5 5.5l9 9M14.5 5.5l-9 9" /></svg>';
  remove.setAttribute("aria-label", "移除这个地址");
  remove.title = "移除这个地址";
  head.append(input, remove);

  const chips = document.createElement("div");
  chips.className = "device-chips";
  for (const id of DEVICE_IDS) {
    const chip = document.createElement("button");
    chip.type = "button";
    chip.className = "device-chip";
    chip.dataset.device = id;
    const selected = (entry.devices || []).includes(id);
    chip.classList.toggle("active", selected);
    chip.setAttribute("aria-pressed", String(selected));
    chip.textContent = DEVICE_LABELS[id];
    chip.addEventListener("click", () => {
      const next = !chip.classList.contains("active");
      chip.classList.toggle("active", next);
      chip.setAttribute("aria-pressed", String(next));
    });
    chips.append(chip);
  }

  const status = document.createElement("small");
  status.className = "entry-status";

  const validate = () => {
    const value = input.value.trim();
    if (!value) {
      setUrlInvalid(input, false);
      status.textContent = "";
      status.hidden = true;
      return "";
    }
    const problem = urlProblem(value, platform);
    setUrlInvalid(input, Boolean(problem));
    status.textContent = problem;
    status.hidden = !problem;
    return problem;
  };
  input.addEventListener("input", validate);
  input.addEventListener("blur", validate);
  remove.addEventListener("click", () => {
    const rows = [...entryList.querySelectorAll(".entry-row")];
    if (rows.length <= 1) {
      // Keep one row so the form never ends up with nothing to fill in.
      input.value = "";
      for (const chip of chips.querySelectorAll(".device-chip")) {
        chip.classList.remove("active");
        chip.setAttribute("aria-pressed", "false");
      }
      validate();
      input.focus();
      return;
    }
    row.remove();
  });

  row.append(head, chips, status);
  row._readEntry = () => ({
    downloadURL: input.value.trim(),
    devices: [...chips.querySelectorAll(".device-chip.active")].map((chip) => chip.dataset.device),
  });
  row._validate = validate;
  row._focus = () => input.focus();
  return row;
}

function addEntryRow(entry) {
  const row = makeEntryRow(entry);
  entryList.append(row);
  return row;
}

function collectEntries() {
  return [...entryList.querySelectorAll(".entry-row")].map((row) => row._readEntry());
}

function resetForm() {
  editingName = "";
  form.reset();
  entryList.replaceChildren();
  addEntryRow();
  setUrlError("");
  const copy = PLATFORM_COPY[platform];
  formTitle.textContent = copy.title;
  submitButton.querySelector(".button-label").textContent = "添加到自有源";
  cancelEditButton.hidden = true;
}

function startEditing(app) {
  editingName = app.name;
  nameInput.value = app.name || "";
  entryList.replaceChildren();
  const entries = app.entries?.length ? app.entries : [{}];
  for (const entry of entries) addEntryRow(entry);
  formTitle.textContent = `编辑 ${app.name}`;
  submitButton.querySelector(".button-label").textContent = "保存修改";
  cancelEditButton.hidden = false;
  setUrlError("");
  message.hidden = true;
  // Editing lives in the form panel, which sits above the list on mobile.
  form.scrollIntoView({ block: "start", behavior: "smooth" });
  nameInput.focus();
}

function checkEntryFields() {
  let firstProblem = "";
  let firstInvalid = null;
  for (const row of entryList.querySelectorAll(".entry-row")) {
    const problem = row._validate();
    if (problem && !firstProblem) {
      firstProblem = problem;
      firstInvalid = row;
    }
  }
  const entries = collectEntries();
  if (!firstProblem && !entries.some((entry) => entry.downloadURL)) {
    firstProblem = `至少填写一个${PLATFORM_COPY[platform].field}`;
    firstInvalid = entryList.querySelector(".entry-row");
  }
  const seen = new Set();
  for (const entry of entries) {
    if (!entry.downloadURL) continue;
    if (seen.has(entry.downloadURL)) {
      firstProblem ||= "同一个应用不能重复登记相同的下载地址";
      break;
    }
    seen.add(entry.downloadURL);
  }
  return { problem: firstProblem, row: firstInvalid };
}

function latestEntry(app) {
  const entries = [...(app.entries || [])];
  entries.sort((a, b) => String(b.date || "").localeCompare(String(a.date || "")));
  return entries[0] || {};
}

function applyPlatformCopy() {
  const copy = PLATFORM_COPY[platform];
  urlFieldLabel.innerHTML = `${copy.field} <span class="required">*</span>`;
  urlFieldHint.textContent = copy.hint;
  footerPath.textContent = copy.path;
  for (const input of entryList.querySelectorAll(".entry-url")) input.placeholder = copy.placeholder;
  // Switching platform must not leave an edit pointed at the other platform.
  if (editingName) resetForm();
  else formTitle.textContent = copy.title;
  for (const row of entryList.querySelectorAll(".entry-row")) row._validate();
  const label = document.querySelector("[data-copy-label]");
  if (label) label.textContent = `复制 ${copy.output}`;
  document.querySelector("button[data-copy-catalog-url]")?.setAttribute("data-platform", platform);
  // Keep <html data-platform> in step so the prepaint highlight stays correct
  // and the copy button reads the same platform this script renders.
  document.documentElement.dataset.platform = platform;
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
    const entry = latestEntry(app);
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
    versionLabel.textContent = `v${entry.version || "—"}`;
    const identifier = document.createElement("span");
    identifier.textContent = app[copy.identifier] || "元数据待解析";
    identifier.title = identifier.textContent;
    meta.append(versionLabel, identifier);
    const entryCount = (app.entries || []).length;
    if (entryCount > 1) {
      const multi = document.createElement("span");
      multi.textContent = `${entryCount} 个地址`;
      meta.append(multi);
    }
    info.append(title, meta);
    const failed = (app.entries || []).find((item) => item.metadataError);
    if (failed) {
      const failure = document.createElement("p");
      failure.className = "app-error";
      failure.textContent = `解析失败：${failed.metadataError}`;
      failure.title = failed.metadataError;
      info.append(failure);
    }
    const link = document.createElement("a");
    link.className = "source-link";
    link.href = entry.downloadURL || "#";
    link.target = "_blank";
    link.rel = "noreferrer";
    link.textContent = `${copy.field.slice(0, 3)} ↗`;
    link.setAttribute("aria-label", `打开 ${app.name} 的下载链接`);
    const actions = document.createElement("div");
    actions.className = "app-row-actions";
    const editButton = document.createElement("button");
    editButton.className = "edit-button";
    editButton.type = "button";
    editButton.textContent = "编辑";
    editButton.setAttribute("aria-label", `编辑 ${app.name}`);
    editButton.addEventListener("click", () => startEditing(app));
    const deleteButton = document.createElement("button");
    deleteButton.className = "delete-button";
    deleteButton.type = "button";
    deleteButton.textContent = "删除";
    deleteButton.setAttribute("aria-label", `删除 ${app.name}`);
    deleteButton.addEventListener("click", () => deleteApp(app, entry, deleteButton));
    actions.append(link, editButton, deleteButton);
    row.append(icon, info, actions);
    list.append(row);
  }
}

async function deleteApp(app, entry, button) {
  const label = app.name || "这个应用";
  const entryCount = (app.entries || []).length;
  const confirmed = await window.appConfirm({
    title: `删除「${label}」？`,
    message: `会从自定义源移除该应用${entryCount > 1 ? `及其 ${entryCount} 个已登记地址` : ""}，下次目录生成后不再出现。`,
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
        downloadURL: entry.downloadURL || "",
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
  const name = nameInput.value.trim();
  if (!name) {
    setMessage("请填写应用名称。");
    nameInput.focus();
    return;
  }
  const check = checkEntryFields();
  if (check.problem) {
    setMessage(check.problem);
    check.row?._focus();
    return;
  }
  const entries = collectEntries().filter((entry) => entry.downloadURL);
  const wasEditing = Boolean(editingName);
  submitButton.disabled = true;
  submitButton.querySelector(".button-label").textContent = "提交并排入解析…";
  try {
    const response = await fetch("/api/apps", {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify({ platform, name, originalName: editingName, entries }),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || `提交失败 (${response.status})`);
    const summary = `${wasEditing ? "已保存" : result.updated ? "已更新" : "已添加"} ${result.app?.name || name}，GitHub Actions 将自动下载并读取版本信息。`;
    setMessage(result.warning ? `${summary}（${result.warning}）` : summary, "success");
    resetForm();
    await loadApps();
  } catch (error) {
    setMessage(error.message);
  } finally {
    submitButton.disabled = false;
    // Restore the label for the mode we are actually in: a failed edit must not
    // leave the button reading "提交并排入解析…".
    submitButton.querySelector(".button-label").textContent = editingName ? "保存修改" : "添加到自有源";
  }
});

addEntryButton.addEventListener("click", () => {
  const row = addEntryRow();
  row._focus();
});

cancelEditButton.addEventListener("click", () => {
  resetForm();
  setMessage("已取消编辑，表单恢复为新增模式。", "success");
});

for (const tab of platformTabs) {
  tab.addEventListener("click", () => {
    if (tab.dataset.platform === platform) return;
    platform = tab.dataset.platform;
    window.viewPreference?.write("platform", platform);
    message.hidden = true;
    applyPlatformCopy();
    loadApps();
  });
}

refreshButton.addEventListener("click", loadApps);
window.addEventListener("platform-change", (event) => {
  if (event.detail) {
    platform = event.detail;
    window.viewPreference?.write("platform", platform);
    applyPlatformCopy();
    loadApps();
  }
});
resetForm();
applyPlatformCopy();
loadApps();
