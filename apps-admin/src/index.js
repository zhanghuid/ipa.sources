import { createRemoteJWKSet, jwtVerify } from "jose";
const encoder = new TextEncoder();

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

function validateApp(input) {
  const fields = ["name", "downloadURL"];
  for (const field of fields) {
    if (typeof input[field] !== "string" || !input[field].trim()) {
      throw new Error(`${field} 为必填项`);
    }
  }
  if (input.name.trim().length > 120) throw new Error("应用名称不能超过 120 个字符");
  if (input.downloadURL.length > 4096) throw new Error("IPA 下载地址不能超过 4096 个字符");
  let download;
  try { download = new URL(input.downloadURL); } catch { throw new Error("IPA 下载地址无效"); }
  if (download.protocol !== "https:") throw new Error("IPA 下载地址必须使用 HTTPS");
  return {
    name: input.name.trim(),
    bundleIdentifier: "",
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
  const response = await fetch(`https://api.github.com${path}`, {
    ...init,
    headers: {
      Accept: "application/vnd.github+json",
      Authorization: `Bearer ${env.GITHUB_TOKEN}`,
      "X-GitHub-Api-Version": "2022-11-28",
      "User-Agent": "ipa-sources-admin",
      ...(init.body ? { "Content-Type": "application/json" } : {}),
      ...init.headers,
    },
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok && response.status !== 404) {
    throw new Error(body.message || `GitHub API returned ${response.status}`);
  }
  return { response, body };
}

function fileEndpoint(env) {
  const owner = encodeURIComponent(env.GITHUB_OWNER || "");
  const repo = encodeURIComponent(env.GITHUB_REPO || "");
  const path = (env.GITHUB_FILE_PATH || "config/sources/custom/ipas.json")
    .split("/").map(encodeURIComponent).join("/");
  return `/repos/${owner}/${repo}/contents/${path}`;
}

function contentsEndpoint(env, filePath) {
  const owner = encodeURIComponent(env.GITHUB_OWNER || "");
  const repo = encodeURIComponent(env.GITHUB_REPO || "");
  const path = filePath.split("/").map(encodeURIComponent).join("/");
  return `/repos/${owner}/${repo}/contents/${path}`;
}

async function loadGeneratedCatalog(env) {
  const branch = env.GITHUB_BRANCH || "main";
  const { response, body } = await githubRequest(`${contentsEndpoint(env, "apps.json")}?ref=${encodeURIComponent(branch)}`, env);
  if (response.status === 404) throw new Error("仓库根目录尚未生成 apps.json");
  const contents = Uint8Array.from(atob(body.content.replace(/\s/g, "")), (char) => char.charCodeAt(0));
  const catalog = JSON.parse(new TextDecoder().decode(contents));
  if (!Array.isArray(catalog.apps)) throw new Error("apps.json 缺少 apps 数组");
  return catalog;
}

async function loadCatalog(env) {
  const branch = env.GITHUB_BRANCH || "main";
  const { response, body } = await githubRequest(`${fileEndpoint(env)}?ref=${encodeURIComponent(branch)}`, env);
  if (response.status === 404) return { catalog: { name: "Custom Apps", apps: [] }, sha: null };
  const contents = Uint8Array.from(atob(body.content.replace(/\s/g, "")), (char) => char.charCodeAt(0));
  const catalog = JSON.parse(new TextDecoder().decode(contents));
  if (!Array.isArray(catalog.apps)) throw new Error("config/sources/custom/ipas.json 缺少 apps 数组");
  return { catalog, sha: body.sha };
}

async function saveCatalog(catalog, sha, env, appName) {
  const branch = env.GITHUB_BRANCH || "main";
  const bytes = encoder.encode(`${JSON.stringify(catalog, null, 2)}\n`);
  let binary = "";
  for (let offset = 0; offset < bytes.length; offset += 0x8000) {
    binary += String.fromCharCode(...bytes.subarray(offset, offset + 0x8000));
  }
  const content = btoa(binary);
  const payload = {
    message: `content: add ${appName} to custom IPA source`,
    content,
    branch,
    ...(sha ? { sha } : {}),
  };
  await githubRequest(fileEndpoint(env), env, { method: "PUT", body: JSON.stringify(payload) });
}

function queueApp(input, catalog) {
  const index = catalog.apps.findIndex((app) =>
    (app.versions || []).some((version) => version.downloadURL === input.downloadURL));
  const existing = index >= 0 ? catalog.apps[index] : null;
  const versions = [...(existing?.versions || [])];
  const versionIndex = versions.findIndex((version) => version.downloadURL === input.downloadURL);
  const queuedVersion = {
    ...(versionIndex >= 0 ? versions[versionIndex] : {}),
    version: "pending",
    buildVersion: "pending",
    date: new Date().toISOString().slice(0, 10),
    localizedDescription: "等待 GitHub Actions 下载 IPA 并读取版本信息。",
    downloadURL: input.downloadURL,
    size: 0,
    minOSVersion: "14.0",
  };
  if (versionIndex >= 0) versions[versionIndex] = queuedVersion;
  else versions.unshift(queuedVersion);
  const app = {
    ...(existing || {}),
    name: input.name,
    bundleIdentifier: existing?.bundleIdentifier || "",
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

async function handleApi(request, env) {
  await requireAccess(request, env);
  if (request.method === "GET" && new URL(request.url).pathname === "/api/apps") {
    const { catalog } = await loadCatalog(env);
    return json({ apps: catalog.apps });
  }
  if (request.method === "GET" && new URL(request.url).pathname === "/api/catalog") {
    const catalog = await loadGeneratedCatalog(env);
    return json({ source: catalog.source || {}, apps: catalog.apps });
  }
  if (request.method === "POST" && new URL(request.url).pathname === "/api/apps") {
    let input;
    try { input = validateApp(await request.json()); }
    catch (error) { return json({ error: error.message }, 400); }
    const { catalog, sha } = await loadCatalog(env);
    catalog.name ||= "Custom Apps";
    const { app, updated } = queueApp(input, catalog);
    await saveCatalog(catalog, sha, env, input.name);
    return json({ app, updated, metadataPending: true }, 201);
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
        return json({ error: error.message || "请求失败" }, 500);
      }
    }
    return env.ASSETS.fetch(request);
  },
};
