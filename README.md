# Personal IPA Source

An AltSource-compatible catalog merged from [jiz4oh/IPAs](https://raw.githubusercontent.com/jiz4oh/IPAs/master/apps.json) and [bebound/AltGallery](https://raw.githubusercontent.com/bebound/AltGallery/refs/heads/master/all-apps.json). GitHub Actions fetches both catalogs every six hours and updates [`apps.json`](apps.json). Duplicate apps are identified by bundle ID; duplicate versions are identified by version and build, and the entry with the newer release date is kept. App details come from the catalog with the latest dated version.

## Add or edit apps

The two source URLs are configured under `catalogSources` in [`config/apps.json`](config/apps.json). You can add more catalogs there. You can also add directly tracked GitHub Release apps under `apps`; each entry needs a display name, bundle ID, upstream repository (`owner/repo`), and one or more `assetPatterns`. Patterns use shell-style wildcards and match asset filenames case-insensitively. `excludePatterns` can remove unwanted variants. `versionsToKeep` is the number of upstream releases to retain; all matching IPA files in those releases are included.

After pushing this repository to GitHub, enable Actions. The workflow runs on a six-hour schedule, after catalog configuration changes, and on manual dispatch. It commits the generated `apps.json` when the catalog changes. No IPA binaries are copied into this repository.

## Releases

This repository is not an npm package; `package.json` is private and only stores the project version for `bumpp`. Run `npm install` once, then `npm run release` and select the version bump. `bumpp` updates the version, creates a release commit and `v` Git tag, and pushes them. Pushing the tag starts the GitHub Actions release workflow, which creates a GitHub Release with generated release notes. It does not publish to the npm registry.

## Download acceleration

The default accelerator is `https://gh-proxy.org/` (set by `acceleratorBaseURL` in the config). The generator prefixes the original GitHub URL in the format documented by [GH-Proxy](https://gh-proxy.com/docs/github-accelerator), without percent-encoding the full URL. You can override the default with the GitHub Actions variable `ACCELERATOR_BASE_URL` or a local environment variable of the same name. To use your own proxy, [`workers/accelerator.js`](workers/accelerator.js) is an optional Cloudflare Worker with byte-range forwarding, CORS headers, and a GitHub Releases host/path allowlist.

1. Deploy `workers/accelerator.js` as a Cloudflare Worker and bind a custom domain, for example `https://ipa-cdn.example.com`.
2. In this GitHub repository, add the Actions **variable** `ACCELERATOR_BASE_URL` with that origin. No secret is needed.
3. Run **Actions → Update IPA source → Run workflow** once. Generated IPA `downloadURL`s will use the Worker. Remove the override and clear `acceleratorBaseURL` in the config to use direct GitHub URLs.

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
