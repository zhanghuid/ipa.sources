#!/usr/bin/env python3
"""Build an AltSource-compatible catalog from configured GitHub releases."""

from __future__ import annotations

import fnmatch
import json
import os
import re
import sys
import time
import threading
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config/apps.json"
OUTPUT = ROOT / "apps.json"


class Progress:
    """Small terminal progress display that also behaves cleanly in GitHub Actions."""

    def __init__(self, label: str, total: int = 100, animate: bool = False) -> None:
        self.label = label
        self.total = max(1, total)
        self.animate = animate
        self.current = 0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._started = time.monotonic()
        self._last_bucket = -1

    def start(self) -> None:
        if not sys.stderr.isatty() or not self.animate:
            print(f"{self.label}…", file=sys.stderr, flush=True)
            return
        self._thread = threading.Thread(target=self._animate, daemon=True)
        self._thread.start()

    def _draw(self, done: bool = False) -> None:
        width = 28
        ratio = min(1.0, self.current / self.total)
        filled = int(width * ratio)
        bar = "█" * filled + "░" * (width - filled)
        elapsed = time.monotonic() - self._started
        suffix = f"{elapsed:.1f}s" if done else f"{self.current}/{self.total}"
        print(f"\r{self.label} [{bar}] {ratio * 100:5.1f}% {suffix}", end="\n" if done else "", file=sys.stderr, flush=True)

    def _animate(self) -> None:
        while not self._stop.wait(0.12):
            self._draw()

    def update(self, current: int | None = None, total: int | None = None) -> None:
        if total is not None:
            self.total = max(1, total)
        self.current = self.current + 1 if current is None else current
        if not self._thread:
            if sys.stderr.isatty():
                self._draw()
                return
            bucket = min(10, int(self.current * 10 / self.total))
            if bucket > self._last_bucket:
                self._last_bucket = bucket
                print(f"  {self.label}: {min(100, bucket * 10)}% ({self.current}/{self.total})", file=sys.stderr, flush=True)

    def finish(self, label: str | None = None) -> None:
        if label:
            self.label = label
        self.current = self.total
        self._stop.set()
        if self._thread:
            self._thread.join()
            self._draw(done=True)
        elif sys.stderr.isatty():
            self._draw(done=True)
        else:
            print(f"  完成：{self.label}", file=sys.stderr, flush=True)


def github_json(url: str) -> dict:
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "personal-ipa-source"}
    token = os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def fetch_catalog(url: str) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": "personal-ipa-source"})
    head_request = urllib.request.Request(url, headers={"User-Agent": "personal-ipa-source"}, method="HEAD")
    with urllib.request.urlopen(head_request, timeout=30) as head_response:
        content_length = int(head_response.headers.get("Content-Length", "0") or 0)
    progress = Progress(f"下载 {url.split('/')[-1]}", content_length or 100, animate=True)
    progress.start()
    with urllib.request.urlopen(request, timeout=60) as response:
        content_length = int(response.headers.get("Content-Length", "0") or content_length)
        chunks: list[bytes] = []
        downloaded = 0
        while True:
            chunk = response.read(8 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
            downloaded += len(chunk)
            if content_length:
                progress.update(min(downloaded, content_length), content_length)
    progress.finish()
    catalog = json.loads(b"".join(chunks))
    if not isinstance(catalog.get("apps"), list):
        raise ValueError(f"Catalog at {url} has no apps array")
    return catalog


def latest_version_date(app: dict) -> str:
    return max((str(item.get("date", "")) for item in app.get("versions", [])), default="")


def merge_catalogs(named_catalogs: list[tuple[str, dict]], accelerator: str) -> list[dict]:
    """Merge catalog apps by bundle ID, retaining the newest duplicate version."""
    grouped: dict[str, list[tuple[int, dict, str]]] = {}
    order: list[str] = []
    for source_index, (source_name, catalog) in enumerate(named_catalogs):
        for app in catalog.get("apps", []):
            bundle_id = str(app.get("bundleIdentifier", "")).strip()
            if not bundle_id:
                # AltSource entries should have bundle IDs; use the name to avoid dropping data.
                bundle_id = "name:" + str(app.get("name", "")).strip().casefold()
            if bundle_id == "name:":
                continue
            if bundle_id not in grouped:
                grouped[bundle_id] = []
                order.append(bundle_id)
            grouped[bundle_id].append((source_index, app, source_name))

    merged: list[dict] = []
    for bundle_id in order:
        candidates = grouped[bundle_id]
        # Latest published version date decides which catalog's app metadata is preferred.
        candidates.sort(key=lambda row: (latest_version_date(row[1]), -row[0]), reverse=True)
        result = dict(candidates[0][1])
        versions_by_key: dict[tuple[str, str], tuple[str, dict]] = {}
        for _, app, _ in candidates:
            for version in app.get("versions", []):
                key = (str(version.get("version", "")), str(version.get("buildVersion", "")))
                if not key[0] and not key[1]:
                    key = (str(version.get("downloadURL", "")), "")
                current = versions_by_key.get(key)
                date = str(version.get("date", ""))
                if current is None or date > current[0]:
                    versions_by_key[key] = (date, dict(version))
        versions = [value[1] for value in versions_by_key.values()]
        versions.sort(key=lambda item: (str(item.get("date", "")), str(item.get("version", ""))), reverse=True)
        if accelerator:
            for version in versions:
                download_url = version.get("downloadURL", "")
                parsed_url = urllib.parse.urlparse(download_url)
                if parsed_url.hostname == "github.com" and "/releases/download/" in parsed_url.path:
                    version["downloadURL"] = accelerated_url(download_url, accelerator)
        result["versions"] = versions
        merged.append(result)
    return merged


def matches(filename: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(filename.lower(), pattern.lower()) for pattern in patterns)


def parse_version(tag: str, filename: str) -> tuple[str, str]:
    stem = Path(filename).stem
    # Common IPA names include app_1.2.3+45.ipa and app-1.2.3.ipa.
    match = re.search(r"(?<![A-Za-z])v?(\d+(?:\.\d+){1,4})(?:\+([\w.-]+))?", stem, re.I)
    if match:
        return match.group(1), match.group(2) or "1"
    return tag.lstrip("v") or "0.0.0", "1"


def accelerated_url(url: str, base: str) -> str:
    if not base:
        return url
    # GitHub proxy services expect the source URL as a readable path after the
    # proxy origin (e.g. https://proxy.example/https://github.com/owner/repo/...).
    # Percent-encoding the whole source URL breaks those services and some IPA clients.
    return base.rstrip("/") + "/" + url


def release_versions(app: dict, defaults: dict, accelerator: str) -> list[dict]:
    repo = app["repo"]
    releases: list[dict] = []
    for page in range(1, 11):
        url = f"https://api.github.com/repos/{repo}/releases?per_page=100&page={page}"
        batch = github_json(url)
        if not batch:
            break
        releases.extend(batch)
        if len(batch) < 100:
            break

    patterns = app.get("assetPatterns", ["*.ipa"])
    excludes = app.get("excludePatterns", [])
    versions: list[dict] = []
    seen: set[str] = set()
    limit = int(app.get("versionsToKeep", defaults.get("versionsToKeep", 10)))
    retained_releases = 0
    progress = Progress(f"解析 {repo} Releases", max(1, len(releases)))
    progress.start()
    for release in releases:
        release_assets = [
            asset for asset in release.get("assets", [])
            if matches(asset.get("name", ""), patterns) and not matches(asset.get("name", ""), excludes)
        ]
        if not release_assets:
            progress.update()
            continue
        if retained_releases >= max(1, limit):
            break
        retained_releases += 1
        for asset in release_assets:
            filename = asset.get("name", "")
            direct_url = asset.get("browser_download_url")
            if not direct_url:
                continue
            version, build = parse_version(release.get("tag_name", ""), filename)
            # Distinct assets at the same version are retained; duplicate API entries are not.
            key = direct_url
            if key in seen:
                continue
            seen.add(key)
            item = {
                "version": version,
                "buildVersion": build,
                "date": (release.get("published_at") or "")[:10] or datetime.now(timezone.utc).date().isoformat(),
                "localizedDescription": f"上游源文件：{filename}" + (f"\n\n{release['body'].strip()}" if release.get("body", "").strip() else ""),
                "downloadURL": accelerated_url(direct_url, accelerator),
                "size": asset.get("size", 0),
                "minOSVersion": app.get("minOSVersion", "14.0"),
            }
            versions.append(item)
        progress.update()

    # GitHub API returns newest release first; keep every matching IPA from the newest N releases.
    progress.finish(f"解析 {repo} Releases（{len(versions)} 个 IPA 版本）")
    return versions


def main() -> int:
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    accelerator = (os.getenv("ACCELERATOR_BASE_URL") or config.get("acceleratorBaseURL", "")).strip()
    source = dict(config.get("source", {}))
    catalog = {**source, "apps": []}
    input_catalogs: list[tuple[str, dict]] = []
    remote_sources = config.get("catalogSources", [])
    source_progress = Progress("获取源目录", len(remote_sources) + len(config.get("apps", [])))
    source_progress.start()
    for remote in remote_sources:
        name = remote.get("name", remote["url"])
        print(f"Fetching catalog: {name}")
        input_catalogs.append((name, fetch_catalog(remote["url"])))
        source_progress.update()
    for app in config.get("apps", []):
        if not app.get("repo"):
            raise ValueError(f"App {app.get('name', '<unnamed>')} has no repo")
        versions = release_versions(app, config.get("defaults", {}), accelerator)
        if not versions:
            print(f"warning: no matching IPA assets for {app['repo']}", file=sys.stderr)
            continue
        input_catalogs.append((f"GitHub Releases: {app['repo']}", {"apps": [{
            "name": app["name"],
            "bundleIdentifier": app["bundleIdentifier"],
            "developerName": app.get("developerName", config.get("defaults", {}).get("developerName", "Upstream")),
            "subtitle": app.get("subtitle", "IPA"),
            "localizedDescription": app.get("localizedDescription", "Automatically synced from GitHub Releases."),
            "upstream": {"repo": app["repo"], "assetPatterns": app.get("assetPatterns", ["*.ipa"]), "excludePatterns": app.get("excludePatterns", [])},
            "iconURL": app.get("iconURL", ""),
            "category": app.get("category", config.get("defaults", {}).get("category", "utilities")),
            "versions": versions,
            "appPermissions": {"entitlements": [], "privacy": {}},
        }]}))
        source_progress.update()
    source_progress.finish("获取源目录完成")
    merge_progress = Progress("合并并去重应用和版本", max(1, sum(len(item.get("apps", [])) for _, item in input_catalogs)))
    merge_progress.start()
    catalog["apps"] = merge_catalogs(input_catalogs, accelerator)
    merge_progress.update(len(catalog["apps"]))
    merge_progress.finish(f"合并完成（{len(catalog['apps'])} 个应用）")
    OUTPUT.write_text(json.dumps(catalog, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {len(catalog['apps'])} apps to {OUTPUT}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (urllib.error.URLError, urllib.error.HTTPError, ValueError, KeyError) as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(1)
