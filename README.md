# Personal IPA Source

An AltSource-compatible catalog generated from repository-scoped source files. The source registry is [`config/sources/index.json`](config/sources/index.json); each upstream catalog is synced into `config/sources/<owner>/<repo>.json`, then merged into [`apps.json`](apps.json). GitHub Actions fetches both catalogs every six hours. Duplicate apps are identified by bundle ID; duplicate versions are identified by version and build, and the entry with the newer release date is kept. App details come from the catalog with the latest dated version.

## Add or edit apps

Add or edit upstream catalog sources in [`config/sources/index.json`](config/sources/index.json), using a file path grouped by owner/repository. The generator downloads and stores each latest upstream JSON at that path before merging. Manually submitted apps live in [`config/sources/custom/ipas.json`](config/sources/custom/ipas.json) and are merged automatically. You can also add directly tracked GitHub Release apps under `apps` in [`config/apps.json`](config/apps.json); each entry needs a display name, bundle ID, upstream repository (`owner/repo`), and one or more `assetPatterns`. Patterns use shell-style wildcards and match asset filenames case-insensitively. `excludePatterns` can remove unwanted variants. `versionsToKeep` is the number of upstream releases to retain; all matching IPA files in those releases are included.

### Submit apps through the Cloudflare page

The private admin page is in [`apps-admin/`](apps-admin/). It is a Cloudflare Worker with Static Assets and a small API. Sign in through Cloudflare Access and enter the app name and a direct HTTPS IPA URL. The Worker commits a pending entry to `config/sources/custom/ipas.json`; GitHub Actions downloads the complete IPA, reads its `Info.plist` to fill in the bundle ID, version, build, minimum iOS version, and file size, then regenerates `apps.json`. This does not depend on the IPA host supporting HTTP Range requests. IPA downloads are limited to 2 GiB.

The [`Deploy IPA admin page` workflow](.github/workflows/deploy-admin.yml) deploys the Worker when its code changes. Add GitHub Actions secrets `CLOUDFLARE_API_TOKEN` and `CLOUDFLARE_ACCOUNT_ID` first. The Cloudflare API token needs permission to edit Workers scripts and access the target account. `preview_urls` is disabled to avoid a second unprotected preview hostname.

Deployment setup:

1. Deploy once with `npm run admin:deploy` to create the `ipa-sources-admin.<account>.workers.dev` hostname.
2. In Cloudflare Zero Trust, protect that complete Worker hostname with Access and allow only your identity. Cloudflare Access can use GitHub as its identity provider if desired.
3. Copy the Access team issuer and this application’s audience tag into `ACCESS_ISSUER` and `ACCESS_AUD` in [`apps-admin/wrangler.jsonc`](apps-admin/wrangler.jsonc), then push the change to trigger deployment.
4. Create a fine-grained GitHub token limited to this repository with **Contents: Read and write**, then run `npx wrangler secret put GITHUB_TOKEN --config apps-admin/wrangler.jsonc`. Do not commit the token.

For local UI preview, run `npm run admin:dev`. The API still requires a valid Cloudflare Access JWT, so listing and submitting work after deploying behind Access. IPA hosts only need to provide a direct HTTPS download; GitHub Actions downloads the full file for metadata extraction.

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
