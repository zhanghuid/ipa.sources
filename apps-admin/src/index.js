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
  },
  android: {
    label: "Android",
    file: "config/android/sources/custom/apks.json",
    output: "android.json",
    extension: "apk",
    urlLabel: "APK",
    identifierField: "packageName",
  },
};

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

function validateApp(input, platform) {
  const config = PLATFORMS[platform];
  const fields = ["name", "downloadURL"];
  for (const field of fields) {
    if (typeof input[field] !== "string" || !input[field].trim()) {
      throw new Error(`${field} 为必填项`);
    }
  }
  if (input.name.trim().length > 120) throw new Error("应用名称不能超过 120 个字符");
  if (input.downloadURL.length > 4096) throw new Error(`${config.urlLabel} 下载地址不能超过 4096 个字符`);
  let download;
  try { download = new URL(input.downloadURL); } catch { throw new Error(`${config.urlLabel} 下载地址无效`); }
  if (download.protocol !== "https:") throw new Error(`${config.urlLabel} 下载地址必须使用 HTTPS`);
  // Reject only a clearly wrong archive extension. Plenty of valid links have no
  // extension at all (redirect endpoints), so an unknown suffix must stay allowed.
  const mismatched = platform === "android" ? /\.ipa$/i : /\.apk$/i;
  if (mismatched.test(download.pathname)) {
    const other = platform === "android" ? "IPA" : "APK";
    throw new Error(`当前是 ${config.label} 平台，但地址指向 ${other} 文件`);
  }
  return {
    name: input.name.trim(),
    developerName: "",
    subtitle: "",
    localizedDescription: "",
    iconURL: "",
    category: "utilities",
    downloadURL: download.href,
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
    message: `content: ${action} ${appName} ${action === "add" ? "to" : "from"} ${config.label} custom source`,
    content: btoa(binary),
    branch,
    ...(sha ? { sha } : {}),
  };
  await githubRequest(contentsEndpoint(env, config.file), env, { method: "PUT", body: JSON.stringify(payload) });
}

function queueApp(input, catalog, platform) {
  const config = PLATFORMS[platform];
  const index = catalog.apps.findIndex((app) =>
    (app.versions || []).some((version) => normalizeDownloadURL(version.downloadURL) === input.downloadURL));
  const existing = index >= 0 ? catalog.apps[index] : null;
  const versions = [...(existing?.versions || [])];
  const versionIndex = versions.findIndex((version) => normalizeDownloadURL(version.downloadURL) === input.downloadURL);
  const queuedVersion = {
    ...(versionIndex >= 0 ? versions[versionIndex] : {}),
    version: "pending",
    buildVersion: "pending",
    date: new Date().toISOString().slice(0, 10),
    localizedDescription: `等待 GitHub Actions 下载 ${config.urlLabel} 并读取版本信息。`,
    downloadURL: input.downloadURL,
    size: 0,
  };
  if (platform === "ios") queuedVersion.minOSVersion = "14.0";
  if (versionIndex >= 0) versions[versionIndex] = queuedVersion;
  else versions.unshift(queuedVersion);
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
    appPermissions: existing?.appPermissions || { entitlements: [], privacy: {} },
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
    });
  }

  if (request.method === "GET" && url.pathname === "/api/apps") {
    const platform = resolvePlatform(url.searchParams.get("platform"));
    if (!platform) return json({ error: "未知平台" }, 400);
    const { catalog } = await loadCatalog(env, platform);
    return json({ platform, apps: catalog.apps });
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
    await saveCatalog(catalog, sha, env, platform, input.name);
    return json({ app, platform, updated, metadataPending: true }, 201);
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
