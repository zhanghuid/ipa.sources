import { createRemoteJWKSet, jwtVerify } from "jose";
const encoder = new TextEncoder();

// Each platform keeps its own source file, generated catalog, and icon folder.
const PLATFORMS = {
  ios: {
    label: "iOS",
    file: "config/ios/sources/custom/ipas.json",
    output: "apps.json",
    extension: "ipa",
    urlLabel: "IPA",
    identifierField: "bundleIdentifier",
    rejectedExtensions: ["apk", "apks", "xapk"],
  },
  android: {
    label: "Android",
    file: "config/android/sources/custom/apks.json",
    output: "android.json",
    extension: "apk",
    urlLabel: "APK",
    identifierField: "packageName",
    rejectedExtensions: ["ipa"],
  },
};

// Web pages that look like a download link but never serve the archive itself.
const STORE_PAGES = [
  { host: "apps.apple.com", reason: "App Store 应用页面不是可直接下载的 IPA 文件" },
  { host: "itunes.apple.com", reason: "iTunes 页面不是可直接下载的 IPA 文件" },
  { host: "testflight.apple.com", reason: "TestFlight 邀请链接不是可直接下载的 IPA 文件" },
  { host: "play.google.com", reason: "Google Play 页面不是可直接下载的 APK 文件" },
];

// Split/bundle formats are zip containers too, but not a single installable APK.
const UNSUPPORTED_ANDROID_EXTENSIONS = { apks: true, xapk: true };

// GitHub pages that render a file listing or metadata instead of the archive.
const GITHUB_NON_FILE_PATHS = [
  {
    pattern: /\/(tree|blob|commits|commit|issues|pull|discussions|wiki|actions)(\/|$)/,
    reason: "GitHub 页面不是文件直链，请填写文件本身的下载地址",
  },
  {
    pattern: /\/releases(\/(tag|latest)(\/[^/]+)?)?\/?$/,
    reason: "GitHub Release 页面不是文件直链，请填写具体资产的下载地址（含 /releases/download/）",
  },
];

// GitHub signs asset links with an expiry; they stop working minutes later.
const TEMPORARY_ASSET_HOSTS = [
  "release-assets.githubusercontent.com",
  "objects.githubusercontent.com",
  "github-cloud.s3.amazonaws.com",
];

const TEMPORARY_ASSET_MESSAGE =
  "这是 GitHub 的临时签名下载链接，过期后会失效；请改用 /releases/download/ 形式的稳定地址";

// Device classes shared with the catalog UI and the generators.
const DEVICE_IDS = ["phone", "pad", "tv", "watch", "mac", "vision"];

function resolvePlatform(value) {
  const key = String(value || "ios").trim().toLowerCase();
  return PLATFORMS[key] ? key : null;
}

function platformConfig(env, platform) {
  const config = PLATFORMS[platform];
  const override = platform === "android"
    ? env.GITHUB_FILE_PATH_ANDROID
    : env.GITHUB_FILE_PATH_IOS || env.GITHUB_FILE_PATH;
  const overrideOutput = platform === "android" ? env.GITHUB_OUTPUT_ANDROID : env.GITHUB_OUTPUT_IOS;
  return {
    ...config,
    file: (override || config.file).trim(),
    output: (overrideOutput || config.output).trim(),
  };
}

function json(data, status = 200) {
  return Response.json(data, {
    status,
    headers: { "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff" },
  });
}

async function requireAccess(request, env) {
  if (!env.ACCESS_ISSUER || !env.ACCESS_AUD || env.ACCESS_AUD.startsWith("SET-")) {
    throw json({ error: "Cloudflare Access 尚未配置。" }, 503);
  }
  const token = request.headers.get("Cf-Access-Jwt-Assertion");
  if (!token) throw json({ error: "请先通过 Cloudflare Access 登录。" }, 401);
  const jwksUrl = new URL("/cdn-cgi/access/certs", env.ACCESS_ISSUER);
  const jwks = createRemoteJWKSet(jwksUrl);
  try {
    await jwtVerify(token, jwks, { issuer: env.ACCESS_ISSUER, audience: env.ACCESS_AUD });
  } catch {
    throw json({ error: "Cloudflare Access 登录状态无效，请刷新登录。" }, 401);
  }
}

function fileExtension(pathname) {
  const last = pathname.split("/").filter(Boolean).pop() || "";
  const match = last.match(/\.([A-Za-z0-9]{1,10})$/);
  return match ? match[1].toLowerCase() : "";
}

function isGitHubHost(hostname) {
  return hostname === "github.com" || hostname.endsWith(".github.com")
    || hostname === "githubusercontent.com" || hostname.endsWith(".githubusercontent.com");
}

function signedQuery(url) {
  const params = url.searchParams;
  return params.has("X-Amz-Signature") || params.has("X-Amz-Credential")
    || (params.has("sig") && params.has("se"));
}

/** Reject page links that never serve the archive, and expiring signed URLs. */
function validateHostSpecifics(download, config) {
  const hostname = download.hostname.toLowerCase();
  const pathname = download.pathname;

  if (TEMPORARY_ASSET_HOSTS.includes(hostname)) {
    return { problem: TEMPORARY_ASSET_MESSAGE };
  }
  if (!isGitHubHost(hostname)) {
    return signedQuery(download) ? { warning: "该地址带签名参数，过期后可能无法下载，建议改用稳定地址" } : {};
  }
  for (const rule of GITHUB_NON_FILE_PATHS) {
    if (rule.pattern.test(pathname)) return { problem: rule.reason };
  }
  if (signedQuery(download)) return { problem: TEMPORARY_ASSET_MESSAGE };
  // /raw/ and raw.githubusercontent.com URLs that end without a file extension
  // are directory listings, which download as HTML instead of an archive.
  const rawLike = hostname === "raw.githubusercontent.com" || /\/(raw|resolve)\//.test(pathname);
  if (rawLike && !fileExtension(pathname)) {
    return {
      problem: `这个 GitHub 地址指向目录，不是 ${config.urlLabel} 文件；请填写直达文件的地址（以 .${config.extension} 结尾）`,
    };
  }
  return {};
}

function validateDownload(download, platform, config) {
  const hostname = download.hostname.toLowerCase();
  const store = STORE_PAGES.find((page) => hostname === page.host || hostname.endsWith(`.${page.host}`));
  if (store) throw new Error(store.reason);

  const hostCheck = validateHostSpecifics(download, config);
  if (hostCheck.problem) throw new Error(hostCheck.problem);

  const extension = fileExtension(download.pathname);
  const other = platform === "android" ? "IPA" : "APK";
  if (config.rejectedExtensions.includes(extension)) {
    throw new Error(`当前是 ${config.label} 平台，但地址指向 ${other} 文件（.${extension}）`);
  }
  if (platform === "android" && UNSUPPORTED_ANDROID_EXTENSIONS[extension]) {
    throw new Error(`暂不支持 .${extension} 分包格式，请填写单独的 APK 直链`);
  }
  // A matching extension is strong evidence; no extension is common for redirect
  // endpoints, so it stays allowed and the generator verifies the real file later.
  const warning = hostCheck.warning || "";
  if (extension === config.extension) return { extension, confident: true, warning };
  if (!extension) return { extension, confident: false, warning };
  return { extension, confident: false, unknownExtension: extension, warning };
}

function normalizeDevices(value) {
  if (value === undefined || value === null) return null;
  if (!Array.isArray(value)) throw new Error("端标签格式无效");
  const seen = [];
  for (const item of value) {
    const id = String(item || "").trim().toLowerCase();
    if (!id) continue;
    if (!DEVICE_IDS.includes(id)) throw new Error(`未知的端标签：${id}`);
    if (!seen.includes(id)) seen.push(id);
  }
  // Canonical order so the stored file stays stable regardless of click order.
  return DEVICE_IDS.filter((id) => seen.includes(id));
}

function validateEntry(raw, platform, config) {
  const value = String(raw?.downloadURL ?? raw ?? "").trim();
  if (!value) throw new Error(`${config.urlLabel} 下载地址不能为空`);
  if (value.length > 4096) throw new Error(`${config.urlLabel} 下载地址不能超过 4096 个字符`);
  let download;
  try { download = new URL(value); } catch { throw new Error(`${config.urlLabel} 下载地址无效`); }
  if (download.protocol !== "https:") throw new Error(`${config.urlLabel} 下载地址必须使用 HTTPS`);
  const check = validateDownload(download, platform, config);
  const warnings = [];
  if (check.unknownExtension) {
    warnings.push(`地址后缀是 .${check.unknownExtension}，不是 .${config.extension}；提交后会在下载解析阶段校验真实文件`);
  }
  if (check.warning) warnings.push(check.warning);
  return {
    downloadURL: download.href,
    devices: normalizeDevices(raw?.devices),
    warnings,
  };
}

function validateApp(input, platform) {
  const config = PLATFORMS[platform];
  if (typeof input.name !== "string" || !input.name.trim()) throw new Error("name 为必填项");
  if (input.name.trim().length > 120) throw new Error("应用名称不能超过 120 个字符");

  // Accept the older single-URL shape so existing links and bookmarks keep working.
  const rawEntries = Array.isArray(input.entries)
    ? input.entries
    : [{ downloadURL: input.downloadURL, devices: input.devices }];
  if (!rawEntries.length) throw new Error("至少需要填写一个下载地址");
  if (rawEntries.length > 20) throw new Error(`一条应用最多登记 20 个地址，当前 ${rawEntries.length} 个`);

  const entries = [];
  const warnings = [];
  const seen = new Set();
  for (const raw of rawEntries) {
    const entry = validateEntry(raw, platform, config);
    const key = normalizeDownloadURL(entry.downloadURL);
    if (seen.has(key)) throw new Error("同一个应用下不能重复登记相同的下载地址");
    seen.add(key);
    entries.push(entry);
    for (const warning of entry.warnings) if (!warnings.includes(warning)) warnings.push(warning);
  }

  return {
    name: input.name.trim(),
    originalName: typeof input.originalName === "string" ? input.originalName.trim() : "",
    entries,
    warnings,
  };
}

async function githubRequest(path, env, init = {}) {
  if (!env.GITHUB_TOKEN) throw new Error("缺少 Worker secret：GITHUB_TOKEN");
  let response;
  try {
    response = await fetch(`https://api.github.com${path}`, {
      ...init,
      signal: AbortSignal.timeout(20_000),
      headers: {
        Accept: "application/vnd.github+json",
        Authorization: `Bearer ${env.GITHUB_TOKEN}`,
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "ipa-sources-admin",
        ...(init.body ? { "Content-Type": "application/json" } : {}),
        ...init.headers,
      },
    });
  } catch (error) {
    if (error.name === "TimeoutError" || error.name === "AbortError") {
      throw Object.assign(new Error("读取 GitHub 仓库超时，请稍后重试。"), { status: 504 });
    }
    throw error;
  }
  const body = await response.json().catch(() => ({}));
  if (!response.ok && response.status !== 404) {
    throw new Error(body.message || `GitHub API returned ${response.status}`);
  }
  return { response, body };
}

function contentsEndpoint(env, filePath) {
  const owner = encodeURIComponent(env.GITHUB_OWNER || "");
  const repo = encodeURIComponent(env.GITHUB_REPO || "");
  const path = filePath.split("/").map(encodeURIComponent).join("/");
  return `/repos/${owner}/${repo}/contents/${path}`;
}

function normalizeDownloadURL(value) {
  const raw = String(value || "").trim();
  try {
    const wrapper = new URL(raw);
    // Accelerated links append the source URL to the proxy path; match the URL, not a proxy host.
    const embeddedURL = wrapper.pathname.match(/\/(https?:\/\/.+)$/i)?.[1];
    const candidate = embeddedURL ? `${embeddedURL}${wrapper.search}` : raw;
    const normalized = new URL(candidate);
    normalized.hash = "";
    return normalized.href;
  } catch {
    return raw;
  }
}

function decodeContents(body) {
  const binary = atob(String(body.content || "").replace(/\s/g, ""));
  return JSON.parse(new TextDecoder().decode(Uint8Array.from(binary, (char) => char.charCodeAt(0))));
}

async function loadGeneratedCatalog(env, platform) {
  const config = platformConfig(env, platform);
  const branch = env.GITHUB_BRANCH || "main";
  const { response, body } = await githubRequest(
    `${contentsEndpoint(env, config.output)}?ref=${encodeURIComponent(branch)}`, env
  );
  if (response.status === 404) throw new Error(`仓库中尚未生成 ${config.output}`);
  const catalog = decodeContents(body);
  if (!Array.isArray(catalog.apps)) throw new Error(`${config.output} 缺少 apps 数组`);
  return catalog;
}

async function loadCatalog(env, platform) {
  const config = platformConfig(env, platform);
  const branch = env.GITHUB_BRANCH || "main";
  const { response, body } = await githubRequest(
    `${contentsEndpoint(env, config.file)}?ref=${encodeURIComponent(branch)}`, env
  );
  if (response.status === 404) return { catalog: { name: "Custom Apps", apps: [] }, sha: null };
  const catalog = decodeContents(body);
  if (!Array.isArray(catalog.apps)) throw new Error(`${config.file} 缺少 apps 数组`);
  return { catalog, sha: body.sha };
}

async function saveCatalog(catalog, sha, env, platform, appName, action = "add") {
  const config = platformConfig(env, platform);
  const branch = env.GITHUB_BRANCH || "main";
  const bytes = encoder.encode(`${JSON.stringify(catalog, null, 2)}\n`);
  let binary = "";
  for (let offset = 0; offset < bytes.length; offset += 0x8000) {
    binary += String.fromCharCode(...bytes.subarray(offset, offset + 0x8000));
  }
  const payload = {
    message: `content: ${action} ${appName} ${action === "remove" ? "from" : "in"} ${config.label} custom source`,
    content: btoa(binary),
    branch,
    ...(sha ? { sha } : {}),
  };
  await githubRequest(contentsEndpoint(env, config.file), env, { method: "PUT", body: JSON.stringify(payload) });
}

function findTargetApp(input, catalog, requestedKeys) {
  const normalized = (value) => String(value || "").trim().toLowerCase();
  // Editing targets the entry the client loaded, matched by its previous name.
  if (input.originalName) {
    const index = catalog.apps.findIndex((app) => normalized(app.name) === normalized(input.originalName));
    if (index >= 0) return index;
  }
  // Then an app that already owns one of the submitted addresses.
  const byURL = catalog.apps.findIndex((app) =>
    (app.versions || []).some((version) => requestedKeys.has(normalizeDownloadURL(version.downloadURL))));
  if (byURL >= 0) return byURL;
  // Finally the display name, so adding a build to a known app appends a version.
  return catalog.apps.findIndex((app) => normalized(app.name) === normalized(input.name));
}

function queueApp(input, catalog, platform) {
  const config = PLATFORMS[platform];
  const requested = input.entries.map((entry) => ({
    ...entry,
    key: normalizeDownloadURL(entry.downloadURL),
  }));
  const requestedKeys = new Set(requested.map((entry) => entry.key));
  const index = findTargetApp(input, catalog, requestedKeys);
  const existing = index >= 0 ? catalog.apps[index] : null;
  const priorVersions = existing?.versions || [];
  const today = new Date().toISOString().slice(0, 10);
  // Editing replaces the address list the form showed, so a removed row is a
  // removal. Adding only appends, which keeps existing addresses and versions.
  const editing = Boolean(input.originalName);
  const requestedByKey = new Map(requested.map((entry) => [entry.key, entry]));
  const kept = editing ? [] : priorVersions.filter(
    (version) => !requestedByKey.has(normalizeDownloadURL(version.downloadURL)));

  // One version record per address. An address that was already resolved keeps
  // its parsed metadata, so editing device tags does not trigger a re-download.
  const queued = requested.map((entry) => {
    const prior = priorVersions.find(
      (version) => normalizeDownloadURL(version.downloadURL) === entry.key);
    const resolved = Boolean(prior && prior.version && prior.version !== "pending");
    const version = { ...(prior || {}) };
    version.version = resolved ? prior.version : "pending";
    version.buildVersion = resolved ? (prior.buildVersion || "pending") : "pending";
    version.date = prior?.date || today;
    version.localizedDescription = resolved
      ? (prior.localizedDescription || "")
      : `等待 GitHub Actions 下载 ${config.urlLabel} 并读取版本信息。`;
    version.downloadURL = entry.downloadURL;
    version.size = Number(prior?.size) || 0;
    if (!resolved) {
      delete version.metadataError;
      if (platform === "ios" && !version.minOSVersion) version.minOSVersion = "14.0";
    }
    // An explicit device tag overrides what the package manifest reports; an
    // empty selection clears the override and returns to automatic detection.
    if (entry.devices && entry.devices.length) version.devices = entry.devices;
    else delete version.devices;
    return version;
  });
  const versions = [...queued, ...kept];

  const app = {
    ...(existing || {}),
    name: input.name,
    [config.identifierField]: existing?.[config.identifierField] || "",
    developerName: existing?.developerName || "Personal source",
    subtitle: existing?.subtitle || "",
    localizedDescription: existing?.localizedDescription || "",
    iconURL: existing?.iconURL || "",
    category: existing?.category || "utilities",
    versions,
    appPermissions: existing?.appPermissions
      || (platform === "android" ? { permissions: [], privacy: {} } : { entitlements: [], privacy: {} }),
  };
  if (index >= 0) catalog.apps[index] = app;
  else catalog.apps.unshift(app);
  return { app, updated: Boolean(existing) };
}

function catalogURL(env, platform) {
  const config = platformConfig(env, platform);
  const owner = String(env.GITHUB_OWNER || "").trim();
  const repo = String(env.GITHUB_REPO || "").trim();
  const branch = String(env.GITHUB_BRANCH || "main").trim();
  if (!owner || !repo) throw Object.assign(new Error("缺少 GitHub 仓库配置"), { status: 500 });
  return `https://raw.githubusercontent.com/${encodeURIComponent(owner)}/${encodeURIComponent(repo)}/${encodeURIComponent(branch)}/${config.output}`;
}

async function handleApi(request, env) {
  await requireAccess(request, env);
  const url = new URL(request.url);

  if (request.method === "GET" && url.pathname === "/api/config") {
    return json({
      platforms: Object.entries(PLATFORMS).map(([key, value]) => ({
        id: key,
        label: value.label,
        urlLabel: value.urlLabel,
        extension: value.extension,
      })),
      devices: DEVICE_IDS,
    });
  }

  if (request.method === "GET" && url.pathname === "/api/apps") {
    const platform = resolvePlatform(url.searchParams.get("platform"));
    if (!platform) return json({ error: "未知平台" }, 400);
    const { catalog } = await loadCatalog(env, platform);
    // The admin list only needs the latest state per app, so expose a compact
    // shape instead of the whole version history it never renders.
    const apps = catalog.apps.map((app) => ({
      name: app.name,
      [PLATFORMS[platform].identifierField]: app[PLATFORMS[platform].identifierField] || "",
      developerName: app.developerName || "",
      iconURL: app.iconURL || "",
      devices: app.devices || [],
      entries: (app.versions || []).map((version) => ({
        downloadURL: version.downloadURL || "",
        version: version.version || "",
        buildVersion: version.buildVersion || "",
        date: version.date || "",
        size: version.size || 0,
        devices: version.devices || [],
        metadataError: version.metadataError || "",
        pending: !version.version || version.version === "pending",
      })),
    }));
    return json({ platform, apps });
  }

  if (request.method === "GET" && url.pathname === "/api/catalog") {
    const requested = url.searchParams.get("platform");
    if (requested === "all") {
      const catalogs = await Promise.all(Object.keys(PLATFORMS).map(async (key) => {
        try {
          return await loadGeneratedCatalog(env, key);
        } catch (error) {
          console.log(JSON.stringify({ event: "catalog_platform_error", platform: key, message: error.message }));
          return { platform: key, apps: [] };
        }
      }));
      return json({
        platforms: Object.fromEntries(catalogs.map((item, index) => [Object.keys(PLATFORMS)[index], item])),
      });
    }
    const platform = resolvePlatform(requested);
    if (!platform) return json({ error: "未知平台" }, 400);
    const catalog = await loadGeneratedCatalog(env, platform);
    return json({ platform, source: catalog.source || {}, apps: catalog.apps });
  }

  if (request.method === "GET" && url.pathname === "/api/catalog-url") {
    const urls = {};
    for (const key of Object.keys(PLATFORMS)) urls[key] = catalogURL(env, key);
    return json({ urls, url: urls.ios });
  }

  if (request.method === "POST" && url.pathname === "/api/apps") {
    let payload;
    try { payload = await request.json(); }
    catch { return json({ error: "提交内容无效" }, 400); }
    const platform = resolvePlatform(payload.platform);
    if (!platform) return json({ error: "未知平台" }, 400);
    let input;
    try { input = validateApp(payload, platform); }
    catch (error) { return json({ error: error.message }, 400); }
    const { catalog, sha } = await loadCatalog(env, platform);
    catalog.name ||= "Custom Apps";
    const { app, updated } = queueApp(input, catalog, platform);
    await saveCatalog(catalog, sha, env, platform, input.name, updated ? "update" : "add");
    return json({
      app: { name: app.name, entries: app.versions.length },
      platform,
      updated,
      metadataPending: true,
      warning: input.warnings.join(" "),
    }, updated ? 200 : 201);
  }

  if (request.method === "DELETE" && url.pathname === "/api/apps") {
    let input;
    try { input = await request.json(); }
    catch { return json({ error: "删除请求内容无效" }, 400); }
    const platform = resolvePlatform(input.platform);
    if (!platform) return json({ error: "未知平台" }, 400);
    const config = PLATFORMS[platform];
    if (typeof input.name !== "string" || !input.name.trim()) {
      return json({ error: "缺少应用名称" }, 400);
    }
    const name = input.name.trim();
    const identifier = typeof input[config.identifierField] === "string"
      ? input[config.identifierField].trim()
      : "";
    const downloadURL = typeof input.downloadURL === "string" ? input.downloadURL.trim() : "";
    if (!identifier && !downloadURL) {
      return json({ error: "缺少应用标识，无法安全删除" }, 400);
    }
    const { catalog, sha } = await loadCatalog(env, platform);
    const index = catalog.apps.findIndex((app) => {
      if (app.name !== name) return false;
      if (identifier) return app[config.identifierField] === identifier;
      return !downloadURL || (app.versions || []).some((version) =>
        normalizeDownloadURL(version.downloadURL) === normalizeDownloadURL(downloadURL));
    });
    if (index < 0) return json({ error: "应用已不存在，请刷新列表后重试" }, 404);
    const [deleted] = catalog.apps.splice(index, 1);
    await saveCatalog(catalog, sha, env, platform, deleted.name, "remove");
    return json({ deleted: deleted.name, platform });
  }

  return json({ error: "Not found" }, 404);
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.pathname.startsWith("/api/")) {
      try { return await handleApi(request, env); }
      catch (error) {
        if (error instanceof Response) return error;
        console.error(JSON.stringify({ event: "admin_api_error", message: error.message }));
        return json({ error: error.message || "请求失败" }, error.status || 500);
      }
    }
    return env.ASSETS.fetch(request);
  },
};
