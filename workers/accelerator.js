/**
 * Optional Cloudflare Worker download accelerator.
 * Deploy with a custom domain, then set GitHub Actions variable
 * ACCELERATOR_BASE_URL to that origin, e.g. https://ipa-cdn.example.com.
 * The generator appends a percent-encoded GitHub release URL.
 */
const ALLOWED_HOSTS = new Set(["github.com", "objects.githubusercontent.com", "release-assets.githubusercontent.com"]);

export default {
  async fetch(request, env, ctx) {
    const cors = { "Access-Control-Allow-Origin": "*", "Access-Control-Allow-Methods": "GET, HEAD, OPTIONS", "Access-Control-Allow-Headers": "Range, If-Range, If-None-Match" };
    if (request.method === "OPTIONS") return new Response(null, { headers: cors });
    if (!["GET", "HEAD"].includes(request.method)) return new Response("Method not allowed", { status: 405 });

    let raw = new URL(request.url).pathname.slice(1);
    // Accept legacy percent-encoded proxy paths while preserving percent escapes
    // that belong to the original GitHub URL (such as encoded release tag names).
    if (!raw.startsWith("https://")) {
      try { raw = decodeURIComponent(raw); } catch { return new Response("Invalid URL encoding", { status: 400 }); }
    }
    let target;
    try { target = new URL(raw); } catch { return new Response("Expected an encoded HTTPS URL", { status: 400 }); }
    if (target.protocol !== "https:" || !ALLOWED_HOSTS.has(target.hostname) || !target.pathname.includes("/releases/download/")) {
      return new Response("Only GitHub release assets are allowed", { status: 403 });
    }

    const headers = new Headers();
    for (const name of ["range", "if-range", "if-none-match", "if-modified-since"]) {
      const value = request.headers.get(name);
      if (value) headers.set(name, value);
    }
    const upstream = new Request(target.toString(), { method: request.method, headers, redirect: "follow" });
    const cacheable = request.method === "GET" && !request.headers.has("range");
    const cache = caches.default;
    let response = cacheable ? await cache.match(upstream) : null;
    if (!response) {
      response = await fetch(upstream, { cf: { cacheEverything: true, cacheTtl: 86400 } });
      if (cacheable && response.ok) ctx.waitUntil(cache.put(upstream, response.clone()));
    }
    const resultHeaders = new Headers(response.headers);
    for (const [key, value] of Object.entries(cors)) resultHeaders.set(key, value);
    resultHeaders.set("Cache-Control", response.status === 206 ? "private, no-store" : "public, max-age=86400");
    return new Response(response.body, { status: response.status, statusText: response.statusText, headers: resultHeaders });
  },
};
