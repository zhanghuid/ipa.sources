# Personal App Source

[![Deploy to Cloudflare](https://deploy.workers.cloudflare.com/button)](https://deploy.workers.cloudflare.com/?url=https://github.com/zhanghuid/ipa.sources/tree/main/apps-admin)

A personal app catalog with two independent platforms. iOS produces an AltSource-compatible [`apps.json`](apps.json); Android produces [`android.json`](android.json). Each platform has its own source registry, parser, generated output, and icon folder, so nothing has to be modelled as a special case of the other.

| Platform | Config | Custom submissions | Output | Identifier |
| --- | --- | --- | --- | --- |
| iOS / tvOS | `config/ios/catalog.json` | `config/ios/sources/custom/ipas.json` | `apps.json` | Bundle ID |
| Android | `config/android/catalog.json` | `config/android/sources/custom/apks.json` | `android.json` | Package name |

Both platforms record a `devices` array so one app can belong to several device classes. GitHub Actions regenerates both catalogs every six hours.

## Device classes

`devices` uses one vocabulary across platforms: `phone`, `pad`, `tv`, `watch`, `mac`, `vision`. The catalog UI filters by these values.

- iOS: read from `UIDeviceFamily` in the IPA's `Info.plist` (1=phone, 2=pad, 3=tv, 4=watch, 6=mac, 7=vision).
- Android: derived from `uses-feature` declarations in the binary `AndroidManifest.xml`. `android.software.leanback`, or a `android.hardware.touchscreen` that is not required, marks a TV app.

Override anything in `deviceOverrides`, keyed by Bundle ID or package name, when a manifest is wrong or missing.

## Add or edit apps

Add upstream catalog sources in `config/<platform>/sources/index.json`, using a file path grouped by owner/repository. The generator downloads and stores each latest upstream JSON at that path before merging.

Directly tracked GitHub Release apps live under `apps` in the platform's `catalog.json`. Each entry needs a display name, an upstream repository (`owner/repo`), and one or more `assetPatterns`. Patterns use shell-style wildcards and match asset filenames case-insensitively. `excludePatterns` removes unwanted variants. `versionsToKeep` is the number of upstream releases to retain.

For Android, tracked entries do not need a package name up front: the generator downloads the newest matching APK once, reads its manifest, then caches the package name, device classes, launcher icon, per-asset `versionCode`, and `minSdk` back into `config/android/catalog.json`. This costs one APK download per app when its newest asset changes.

## How metadata is extracted

Neither parser uses HTTP Range requests, so no host has to support partial downloads. Both download the complete file and parse it locally; downloads are limited to 2 GiB.

- **iOS**: unzip the IPA, read `Payload/*.app/Info.plist`, and take `CFBundleIdentifier`, `CFBundleShortVersionString`, `CFBundleVersion`, `MinimumOSVersion`, and `UIDeviceFamily`. The largest declared primary icon PNG is extracted, and Apple's CgBI PNG variant is converted to a standard PNG.
- **Android**: read `AndroidManifest.xml` and `resources.arsc`, both binary formats, using the self-contained reader in `scripts/apk_metadata.py`. It resolves the launcher icon through the resource table, follows adaptive-icon layers, and converts WebP icons to PNG.

Icon conversion uses Pillow, which the GitHub Actions workflow installs. Without Pillow the parsers still work and skip non-PNG icons.

### Submit apps through the Cloudflare page

The private admin page is in [`apps-admin/`](apps-admin/). It is a Cloudflare Worker with Static Assets and a small API. Sign in through Cloudflare Access, pick a platform, then enter the app name and a direct HTTPS download URL. The Worker commits a pending entry to that platform's custom source file; GitHub Actions downloads the complete package, extracts its metadata, and regenerates the platform's catalog. Delete uses the same platform-scoped path. On the iOS side, App Store artwork is used as a fallback when no bundle icon is present.

The page has a platform switcher, and the full catalog at `/catalog.html` shows both platforms with device filter chips (phone, pad, TV, watch, vision).

Submissions are checked in three places, so a wrong link fails at the earliest possible moment:

- **Form (browser)**: instant validation while typing. The field turns red with a specific reason.
- **Worker (API)**: the same rules enforced server-side. The response may include a non-blocking `warning`.
- **Generator (GitHub Actions)**: the real file is downloaded and its internal layout is checked, so a mislabelled archive is caught even when the URL looks right.

The URL checks reject:

| Input | Why |
| --- | --- |
| `.apk` on the iOS tab, `.ipa` on the Android tab | Platform mismatch |
| `.apks` / `.xapk` | Split bundles are not a single installable APK |
| App Store, iTunes, TestFlight, Google Play pages | Store pages never serve the archive |
| `github.com/.../tree/`, `/blob/`, `/releases/tag/...` | GitHub pages return HTML, not the file |
| GitHub `/raw/` paths without a file extension | Directory listing, not a file |
| `release-assets.githubusercontent.com/...&se=...&sig=...` | Signed asset links expire within minutes |

Extensionless URLs on other hosts stay allowed, because redirect endpoints are common; the generator verifies the real bytes and reports a precise error if the response is an HTML page instead of an archive.

Use the **Deploy to Cloudflare** button above for a guided first deployment. Cloudflare creates a separate GitHub repository containing the self-contained Worker from `apps-admin/` (the folder contents become the new repository root) and connects it to Workers Builds. The source repository must be public for this button to work. The initial deployment uses a `workers.dev` address and safe placeholder settings; complete the setup below before using the admin API.

After the button deploys the Worker:

1. In the newly created GitHub repository, edit its root `wrangler.jsonc`: set `GITHUB_OWNER`, `GITHUB_REPO`, and `GITHUB_BRANCH` to the source repository that contains your generated catalogs and `config/*/sources/custom/` files. Push the change to deploy the Worker with your source repository configured.
2. In Cloudflare Zero Trust, create an Access application for the deployed `workers.dev` hostname and allow only your identity. You can use GitHub as the identity provider. Copy its team issuer and audience tag into `ACCESS_ISSUER` and `ACCESS_AUD` in `wrangler.jsonc`, then push the change to redeploy. Configure Access before using the admin API.
3. Create a fine-grained GitHub token limited to the source repository with **Contents: Read and write**, then add it to the Worker as the `GITHUB_TOKEN` secret. Never commit this token.

For this repository's own deployment, the [`Deploy IPA admin page` workflow](.github/workflows/deploy-admin.yml) deploys the Worker when its code changes. Add GitHub Actions secrets `CLOUDFLARE_API_TOKEN` and `CLOUDFLARE_ACCOUNT_ID` first. The Cloudflare API token needs permission to edit Workers scripts and access the target account. `preview_urls` is disabled to avoid a second unprotected preview hostname. This repo's personal domain and Access configuration are kept in [`wrangler.production.jsonc`](wrangler.production.jsonc); the generic [`apps-admin/wrangler.jsonc`](apps-admin/wrangler.jsonc) is used by the one-click deployment template.

Deployment setup for this repository:

1. Deploy once with `npm run admin:deploy` to create the configured Worker.
2. In Cloudflare Zero Trust, protect that complete Worker hostname with Access and allow only your identity. Cloudflare Access can use GitHub as its identity provider if desired.
3. Copy the Access team issuer and this application’s audience tag into `ACCESS_ISSUER` and `ACCESS_AUD` in [`wrangler.production.jsonc`](wrangler.production.jsonc), then push the change to trigger deployment.
4. Create a fine-grained GitHub token limited to this repository with **Contents: Read and write**, then run `npx wrangler secret put GITHUB_TOKEN --config wrangler.production.jsonc`. Do not commit the token.

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
python scripts/generate_ios.py
python scripts/generate_android.py   # WebP icon conversion needs Pillow
```

Set `GITHUB_TOKEN` to raise the unauthenticated API rate limit when generating outside Actions. Set `ACCELERATOR_BASE_URL` locally to preview accelerated download URLs.
