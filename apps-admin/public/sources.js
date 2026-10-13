const form = document.querySelector("#source-form");
const list = document.querySelector("#source-list");
const state = document.querySelector("#source-state");
const count = document.querySelector("#source-count");
const message = document.querySelector("#form-message");
const submitButton = document.querySelector("#submit-button");
const refreshButton = document.querySelector("#refresh-button");
const cancelEditButton = document.querySelector("#cancel-edit-button");
const formTitle = document.querySelector("#form-title");
const footerPath = document.querySelector("#footer-path");
const titleInput = document.querySelector("#source-title");
const urlInput = document.querySelector("#source-url");
const noteInput = document.querySelector("#source-note");
const urlFieldHint = document.querySelector("#url-field-hint");
const urlFieldError = document.querySelector("#url-field-error");
const globalEntries = document.querySelector("#global-entries");
const categoryTabs = [...document.querySelectorAll(".category-tab")];

const CATEGORIES = [
  { id: "live", label: "直播源", glyph: "LIVE", placeholder: "https://…/live.m3u" },
  { id: "video", label: "视频源", glyph: "VID", placeholder: "https://…/api.php/provide/vod" },
  { id: "audio", label: "音频源", glyph: "AUD", placeholder: "https://…/music.json" },
  { id: "comic", label: "漫画源", glyph: "COM", placeholder: "https://…/comic.json" },
  { id: "novel", label: "小说源", glyph: "NOV", placeholder: "https://…/novel.json" },
  { id: "aggregate", label: "聚合源", glyph: "AGG", placeholder: "https://…/config.json" },
];

const CATEGORY_BY_ID = Object.fromEntries(CATEGORIES.map((item) => [item.id, item]));
const STATUS_LABELS = {
  unchecked: "未检测",
  available: "可用",
  suspected: "疑似不可用",
  unavailable: "不可用",
  disabled: "已禁用",
};

let activeRequest;
let requestId = 0;
// URL of the source being edited, or "" while adding a new one.
let editingUrl = "";
const categoryIds = CATEGORIES.map((item) => item.id);
let category = window.viewPreference?.read("category", categoryIds, "live") || "live";

function setMessage(text, kind = "error") {
  message.textContent = text;
  message.className = `form-message${kind === "success" ? " success" : ""}`;
  message.hidden = false;
}

function setFieldError(text) {
  urlFieldError.textContent = text || "";
  urlFieldError.hidden = !text;
  if (text) urlInput.setAttribute("aria-invalid", "true");
  else urlInput.removeAttribute("aria-invalid");
}

// Mirrors the Worker rules so a bad address fails while typing, not on submit.
// A local/private host is stored but useless to clients, so it is rejected here too.
function urlProblem(raw) {
  const value = String(raw || "").trim();
  if (!value) return "";
  if (value.length > 4096) return "源地址不能超过 4096 个字符";
  let url;
  try { url = new URL(value); } catch { return "源地址无效，请填写完整的链接"; }
  if (url.protocol !== "http:" && url.protocol !== "https:") return "源地址必须是 http 或 https 链接";
  const hostname = url.hostname.toLowerCase();
  if (/^(localhost|127(\.\d{1,3}){3}|0\.0\.0\.0|\[?::1\]?|.*\.local|.*\.internal)$/i.test(hostname)) {
    return "不能登记本机或内网地址，客户端无法访问";
  }
  return "";
}

function validateURLField() {
  const problem = urlProblem(urlInput.value);
  setFieldError(problem);
  return problem;
}

function describeStatus(source) {
  const label = STATUS_LABELS[source.status] || "未检测";
  if (source.status === "disabled") return `${label}（手动）`;
  if (source.httpStatus) return `${label} · HTTP ${source.httpStatus}`;
  if (source.error) return `${label} · ${source.error}`;
  if (!source.lastCheckedAt) return `${label} · 尚未运行检查脚本`;
  return label;
}

function resetForm() {
  editingUrl = "";
  form.reset();
  setFieldError("");
  const copy = CATEGORY_BY_ID[category];
  formTitle.textContent = `添加${copy.label}`;
  submitButton.querySelector(".button-label").textContent = "添加到订阅源";
  cancelEditButton.hidden = true;
  urlInput.placeholder = copy.placeholder;
  footerPath.textContent = `config/library/${category}.json`;
}

function startEditing(source) {
  editingUrl = source.url;
  titleInput.value = source.title || "";
  urlInput.value = source.url || "";
  noteInput.value = source.note || "";
  formTitle.textContent = `编辑 ${source.title || source.url}`;
  submitButton.querySelector(".button-label").textContent = "保存修改";
  cancelEditButton.hidden = false;
  setFieldError("");
  message.hidden = true;
  form.scrollIntoView({ block: "start", behavior: "smooth" });
  titleInput.focus();
}

function applyCategoryCopy() {
  const copy = CATEGORY_BY_ID[category];
  footerPath.textContent = `config/library/${category}.json`;
  urlInput.placeholder = copy.placeholder;
  globalEntries.hidden = category !== "aggregate";
  if (editingUrl) resetForm();
  else formTitle.textContent = `添加${copy.label}`;
  const label = document.querySelector("[data-copy-library-url] [data-copy-label]");
  if (label) label.textContent = `复制 ${category} 清单`;
  document.querySelector("button[data-copy-library-url]")?.setAttribute("data-category", category);
  document.documentElement.dataset.category = category;
  for (const tab of categoryTabs) {
    const selected = tab.dataset.category === category;
    tab.classList.toggle("active", selected);
    tab.setAttribute("aria-selected", String(selected));
  }
}

function statusClass(source) {
  return `status-${source.status || "unchecked"}`;
}

function renderSources(sources) {
  state.hidden = true;
  list.hidden = false;
  list.replaceChildren();
  count.textContent = String(sources.length).padStart(2, "0");
  const copy = CATEGORY_BY_ID[category];
  if (!sources.length) {
    const empty = document.createElement("div");
    empty.className = "empty-state";
    const strong = document.createElement("strong");
    strong.textContent = `还没有登记${copy.label}`;
    const span = document.createElement("span");
    span.textContent = "填写标题和源地址后，源会显示在这里。";
    empty.append(strong, span);
    list.append(empty);
    return;
  }

  for (const source of sources) {
    const row = document.createElement("article");
    row.className = "app-row source-row";

    const badge = document.createElement("span");
    badge.className = `status-badge ${statusClass(source)}`;
    badge.textContent = STATUS_LABELS[source.status] || "未检测";
    badge.title = describeStatus(source);

    const info = document.createElement("div");
    info.className = "app-info";
    const title = document.createElement("p");
    title.className = "app-name";
    title.textContent = source.title || source.url;
    const meta = document.createElement("div");
    meta.className = "app-meta";
    const host = document.createElement("span");
    try { host.textContent = new URL(source.url).host; } catch { host.textContent = source.url; }
    host.title = source.url;
    const status = document.createElement("span");
    status.className = "source-status-line";
    status.textContent = describeStatus(source);
    meta.append(host, status);
    info.append(title, meta);
    if (source.note) {
      const note = document.createElement("p");
      note.className = "source-note";
      note.textContent = source.note;
      info.append(note);
    }

    const actions = document.createElement("div");
    actions.className = "app-row-actions";
    const link = document.createElement("a");
    link.className = "source-link";
    link.href = source.url;
    link.target = "_blank";
    link.rel = "noreferrer";
    link.textContent = "打开 ↗";
    link.setAttribute("aria-label", `打开 ${source.title || source.url}`);
    const toggle = document.createElement("button");
    toggle.className = "edit-button";
    toggle.type = "button";
    toggle.textContent = source.enabled ? "禁用" : "启用";
    toggle.setAttribute("aria-label", `${source.enabled ? "禁用" : "启用"} ${source.title || source.url}`);
    toggle.addEventListener("click", () => toggleSource(source, toggle));
    const edit = document.createElement("button");
    edit.className = "edit-button";
    edit.type = "button";
    edit.textContent = "编辑";
    edit.setAttribute("aria-label", `编辑 ${source.title || source.url}`);
    edit.addEventListener("click", () => startEditing(source));
    const remove = document.createElement("button");
    remove.className = "delete-button";
    remove.type = "button";
    remove.textContent = "删除";
    remove.setAttribute("aria-label", `删除 ${source.title || source.url}`);
    remove.addEventListener("click", () => deleteSource(source, remove));
    actions.append(link, toggle, edit, remove);
    row.append(badge, info, actions);
    list.append(row);
  }
}

async function toggleSource(source, button) {
  button.disabled = true;
  try {
    const response = await fetch("/api/library", {
      method: "PATCH",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify({ category, url: source.url, enabled: !source.enabled }),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || `操作失败 (${response.status})`);
    setMessage(`${source.title || source.url} 已${source.enabled ? "禁用" : "启用"}。`, "success");
    await loadSources();
  } catch (error) {
    setMessage(error.message || "操作失败，请刷新列表后重试。");
    button.disabled = false;
  }
}

async function deleteSource(source, button) {
  const label = source.title || source.url;
  const confirmed = await window.appConfirm({
    title: `删除「${label}」？`,
    message: "会从订阅源清单中移除该地址，下次聚合生成后不再出现。",
    detail: source.url,
    confirmLabel: "删除源",
    trigger: button,
  });
  if (!confirmed) return;
  button.disabled = true;
  button.textContent = "删除中…";
  try {
    const response = await fetch("/api/library", {
      method: "DELETE",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify({ category, url: source.url }),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || `删除失败 (${response.status})`);
    setMessage(`已删除 ${result.deleted}。`, "success");
    await loadSources();
  } catch (error) {
    setMessage(error.message || "删除失败，请刷新列表后重试。");
    button.disabled = false;
    button.textContent = "删除";
  }
}

async function loadSources() {
  activeRequest?.abort();
  const controller = new AbortController();
  activeRequest = controller;
  const id = ++requestId;
  state.hidden = false;
  state.className = "catalog-state";
  state.replaceChildren();
  const loader = document.createElement("span");
  loader.className = "loader";
  loader.setAttribute("aria-hidden", "true");
  const label = document.createElement("span");
  label.textContent = "正在读取订阅源…";
  state.append(loader, label);
  list.hidden = true;
  let timeoutId;
  try {
    const timeout = new Promise((_, reject) => {
      timeoutId = setTimeout(() => {
        controller.abort();
        const error = new Error("读取源列表超时，请检查网络或 Access 登录状态后重试");
        error.name = "TimeoutError";
        reject(error);
      }, 20_000);
    });
    const request = (async () => {
      const response = await fetch(`/api/library?category=${encodeURIComponent(category)}`, {
        headers: { Accept: "application/json" },
        signal: controller.signal,
      });
      const contentType = response.headers.get("content-type") || "";
      if (!contentType.includes("application/json")) {
        const error = new Error(response.redirected
          ? "Cloudflare Access 登录已过期，请重新登录后刷新页面。"
          : `服务返回了非 JSON 响应 (${response.status})，请稍后重试。`);
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
    renderSources(result.sources || []);
  } catch (error) {
    if (controller.signal.aborted && error.name === "AbortError" && id !== requestId) return;
    state.className = "catalog-state error";
    state.textContent = error.name === "TimeoutError" || error.name === "AbortError"
      ? `${error.message || "读取源列表超时"}。`
      : error.status === 401
      ? `${error.message} 登录后刷新页面。`
      : error.status === 503
        ? `${error.message} 部署前需要配置 Access 团队域名和应用 AUD。`
        : `${error.message || "读取源列表失败"}。可点击右侧刷新按钮重试。`;
    count.textContent = "—";
  } finally {
    clearTimeout(timeoutId);
    if (activeRequest === controller) activeRequest = undefined;
  }
}

async function loadCounts() {
  const results = await Promise.all(CATEGORIES.map(async (item) => {
    try {
      const response = await fetch(`/api/library?category=${encodeURIComponent(item.id)}`, {
        headers: { Accept: "application/json" },
      });
      if (!response.ok) return null;
      const result = await response.json();
      return [item.id, (result.sources || []).length];
    } catch {
      return null;
    }
  }));
  for (const item of results) {
    if (!item) continue;
    const node = document.querySelector(`[data-count-for="${item[0]}"]`);
    if (node) node.textContent = `${item[1]}`;
  }
}

async function loadPublishedURLs() {
  try {
    const response = await fetch("/api/library/config", { headers: { Accept: "application/json" } });
    const result = await response.json();
    if (!response.ok) return;
    const urls = result.urls?.files || {};
    for (const [key, value] of Object.entries(urls)) {
      const node = document.querySelector(`[data-global-url="${key}"]`);
      if (node) node.textContent = value;
    }
  } catch {
    // The global entries are informational; a failure here must not break the page.
  }
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  message.hidden = true;
  const title = titleInput.value.trim();
  if (!title) {
    setMessage("请填写标题。");
    titleInput.focus();
    return;
  }
  const problem = validateURLField();
  if (problem) {
    setMessage(problem);
    urlInput.focus();
    return;
  }
  const wasEditing = Boolean(editingUrl);
  submitButton.disabled = true;
  submitButton.querySelector(".button-label").textContent = "提交中…";
  try {
    const response = await fetch("/api/library", {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify({
        category,
        title,
        url: urlInput.value.trim(),
        note: noteInput.value.trim(),
        originalUrl: editingUrl,
      }),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || `提交失败 (${response.status})`);
    const summary = `${wasEditing ? "已保存" : result.updated ? "已更新" : "已添加"} ${result.source?.title || title}。`;
    setMessage(result.warning ? `${summary}（${result.warning}）` : summary, "success");
    resetForm();
    await loadSources();
    loadCounts();
  } catch (error) {
    setMessage(error.message);
  } finally {
    submitButton.disabled = false;
    submitButton.querySelector(".button-label").textContent = editingUrl ? "保存修改" : "添加到订阅源";
  }
});

cancelEditButton.addEventListener("click", () => {
  resetForm();
  setMessage("已取消编辑，表单恢复为新增模式。", "success");
});

urlInput.addEventListener("input", () => {
  if (urlFieldError.hidden) return;
  validateURLField();
});
urlInput.addEventListener("blur", validateURLField);

for (const tab of categoryTabs) {
  tab.addEventListener("click", () => {
    if (tab.dataset.category === category) return;
    category = tab.dataset.category;
    window.viewPreference?.write("category", category);
    message.hidden = true;
    applyCategoryCopy();
    loadSources();
  });
}

refreshButton.addEventListener("click", loadSources);

resetForm();
applyCategoryCopy();
loadSources();
loadCounts();
loadPublishedURLs();
