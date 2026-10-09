# Personal IPA Source

An AltSource-compatible catalog merged from [jiz4oh/IPAs](https://raw.githubusercontent.com/jiz4oh/IPAs/master/apps.json) and [bebound/AltGallery](https://raw.githubusercontent.com/bebound/AltGallery/refs/heads/master/all-apps.json). GitHub Actions fetches both catalogs every six hours and updates [`apps.json`](apps.json). Duplicate apps are identified by bundle ID; duplicate versions are identified by version and build, and the entry with the newer release date is kept. App details come from the catalog with the latest dated version.

## Add or edit apps

The two source URLs are configured under `catalogSources` in [`config/apps.json`](config/apps.json). You can add more catalogs there. You can also add directly tracked GitHub Release apps under `apps`; each entry needs a display name, bundle ID, upstream repository (`owner/repo`), and one or more `assetPatterns`. Patterns use shell-style wildcards and match asset filenames case-insensitively. `excludePatterns` can remove unwanted variants. `versionsToKeep` is the number of upstream releases to retain; all matching IPA files in those releases are included.

After pushing this repository to GitHub, enable Actions. The workflow runs on a six-hour schedule, after catalog configuration changes, and on manual dispatch. It commits the generated `apps.json` when the catalog changes. No IPA binaries are copied into this repository.

## Download acceleration

The catalog can route GitHub release downloads from both imported catalogs and directly tracked apps through an optional Cloudflare Worker. [`workers/accelerator.js`](workers/accelerator.js) is a streaming proxy with byte-range forwarding, browser CORS headers, one-day edge caching, and a GitHub Releases host/path allowlist.

1. Deploy `workers/accelerator.js` as a Cloudflare Worker and bind a custom domain, for example `https://ipa-cdn.example.com`.
2. In this GitHub repository, add the Actions **variable** `ACCELERATOR_BASE_URL` with that origin. No secret is needed.
3. Run **Actions → Update IPA source → Run workflow** once. Generated IPA `downloadURL`s will use the Worker. Without the variable, the catalog uses the upstream GitHub URLs directly.

The Worker does not store or rehost IPA files. It streams upstream release assets and lets Cloudflare cache eligible full-file responses at its edge. Range requests (used by some download clients) are forwarded and not cached by the Worker cache API.

## Import URL

After enabling GitHub Pages for the repository root (or using another static host), import:

```text
https://<owner>.github.io/<repository>/apps.json
```

You can also use the raw file URL:

```text
https://raw.githubusercontent.com/<owner>/<repository>/main/apps.json
```

## Local generation

Requires Python 3.10+ and network access to the configured catalog URLs and GitHub API:

```bash
python scripts/generate_source.py
```

Set `GITHUB_TOKEN` to raise the unauthenticated API rate limit when generating outside Actions. Set `ACCELERATOR_BASE_URL` locally to preview accelerated download URLs.
