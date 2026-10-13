"""Infrastructure shared by the per-platform catalog generators.

Platform-specific parsing lives in ``generate_ios.py`` (iOS) and
``generate_android.py`` (Android); this module only holds the pieces both need:
progress reporting, GitHub/release access, accelerated URLs, and safe file writes.
"""

from __future__ import annotations

import fnmatch
import json
import os
import re
import struct
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
USER_AGENT = "personal-app-source"


class Progress:
    """Terminal progress display that also behaves cleanly in GitHub Actions."""

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
    headers = {"Accept": "application/vnd.github+json", "User-Agent": USER_AGENT}
    token = os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def fetch_catalog(url: str, label: str) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    head_request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT}, method="HEAD")
    with urllib.request.urlopen(head_request, timeout=30) as head_response:
        content_length = int(head_response.headers.get("Content-Length", "0") or 0)
    progress = Progress(f"下载 {label}", content_length or 100, animate=True)
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


HTML_PREVIEW_SNIFF = 512


def looks_like_html(head: bytes) -> bool:
    """Detect a directory or landing page served in place of the archive.

    GitHub ``/tree/`` pages and similar links answer with HTML and a 200 status,
    which otherwise surfaces as an opaque "not a zip file" parse error far from
    the real cause. Sniffing the first bytes keeps the message actionable.
    """
    start = head.lstrip()[:HTML_PREVIEW_SNIFF].lower()
    return start.startswith(b"<!doctype html") or start.startswith(b"<html")


def download_to_tempfile(url: str, label: str, max_size: int, suffix: str = ".bin") -> tuple[tempfile._TemporaryFileWrapper, int]:
    """Stream a remote file into a temporary file, reporting progress.

    The complete file is downloaded on purpose: neither the IPA nor the APK
    hosts are assumed to support HTTP Range requests.
    """
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https":
        raise ValueError(f"{label}: download URL must use HTTPS")
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    temporary = tempfile.NamedTemporaryFile(prefix="app-source-", suffix=suffix, delete=False)
    progress = Progress(f"下载：{label}", 100)
    progress.start()
    downloaded = 0
    content_length = 0
    download_complete = False
    head = b""
    content_type = ""
    try:
        try:
            with urllib.request.urlopen(request, timeout=300) as response:
                if urllib.parse.urlparse(response.geturl()).scheme != "https":
                    raise ValueError(f"{label}: download redirected to a non-HTTPS URL")
                content_type = (response.headers.get("Content-Type", "") or "").lower()
                if "text/html" in content_type:
                    raise ValueError(
                        f"{label}: the download URL returned an HTML page, not an archive; "
                        "use a direct file URL (a GitHub /tree/ or release page link cannot be parsed)"
                    )
                content_length = int(response.headers.get("Content-Length", "0") or 0)
                if content_length > max_size:
                    raise ValueError(f"{label}: file exceeds the {max_size // (1024 ** 3)} GiB processing limit")
                if content_length:
                    progress.total = content_length
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    if len(head) < HTML_PREVIEW_SNIFF:
                        head += chunk[: HTML_PREVIEW_SNIFF - len(head)]
                        if looks_like_html(head):
                            raise ValueError(
                                f"{label}: the download URL returned an HTML page, not an archive; "
                                "use a direct file URL (a GitHub /tree/ or release page link cannot be parsed)"
                            )
                    downloaded += len(chunk)
                    if downloaded > max_size:
                        raise ValueError(f"{label}: file exceeds the {max_size // (1024 ** 3)} GiB processing limit")
                    temporary.write(chunk)
                    if content_length:
                        progress.update(min(downloaded, content_length), content_length)
                    elif downloaded % (16 * 1024 * 1024) < 1024 * 1024:
                        print(f"  {label}: 已下载 {downloaded // (1024 * 1024)} MiB", file=sys.stderr, flush=True)
                # A connection that closes early still looks like a clean EOF, so
                # compare against the advertised length before trusting the bytes.
                if content_length and downloaded != content_length:
                    raise ValueError(
                        f"{label}: download is incomplete "
                        f"({downloaded}/{content_length} bytes); retry the run"
                    )
            download_complete = True
        finally:
            outcome = "下载完成" if download_complete else "下载中断"
            progress.finish(f"{outcome}：{label}（{downloaded / (1024 * 1024):.1f} MiB）")
        temporary.flush()
    except BaseException:
        temporary.close()
        Path(temporary.name).unlink(missing_ok=True)
        raise
    return temporary, downloaded


def store_icon(icon_dir: Path, identifier: str, image: bytes, repository: str, branch: str) -> str:
    """Persist an extracted icon and return its public raw GitHub URL."""
    if not re.fullmatch(r"[A-Za-z0-9_.-]+(?:\.[A-Za-z0-9_.-]+)+", identifier):
        raise ValueError("refusing to store an icon with an invalid identifier")
    if len(image) > 12 * 1024 * 1024:
        raise ValueError(f"{identifier}: extracted icon exceeds the 12 MiB limit")
    icon_dir.mkdir(parents=True, exist_ok=True)
    target = icon_dir / f"{identifier}.png"
    if not target.exists() or target.read_bytes() != image:
        temporary = target.with_suffix(".png.tmp")
        temporary.write_bytes(image)
        temporary.replace(target)
    relative = icon_dir.relative_to(ROOT).as_posix()
    return f"https://raw.githubusercontent.com/{repository}/{branch}/{relative}/{identifier}.png"


def write_json_atomic(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def classify_archive(names: list[str]) -> str:
    """Identify an app archive by its internal layout.

    IPAs and APKs are both ZIP containers, so the archive members are the only
    reliable signal. Used to report a platform/format mismatch precisely instead
    of a generic parse failure.
    """
    ipa_layout = any(
        re.fullmatch(r"Payload/[^/]+\.app/Info\.plist", name) for name in names
    )
    apk_layout = any(
        name in ("AndroidManifest.xml", "resources.arsc") for name in names
    )
    if ipa_layout and not apk_layout:
        return "ipa"
    if apk_layout and not ipa_layout:
        return "apk"
    if ipa_layout and apk_layout:
        return "ambiguous"
    return "unknown"


def format_mismatch_message(label: str, expected: str, actual: str) -> str | None:
    """Explain a wrong-platform upload, or return None when the file looks right."""
    if actual == "unknown" or actual == "ambiguous" or actual == expected:
        return None
    other = "IPA" if actual == "ipa" else "APK"
    platform = "iOS" if actual == "ipa" else "Android"
    return (
        f"{label}: this looks like an {other} file. "
        f"Submit it on the {platform} platform instead."
    )


DEVICE_ORDER = ["phone", "pad", "tv", "watch", "mac", "vision"]


def normalize_devices(value: object) -> list[str]:
    """Keep only known device classes, in one stable order.

    Manual tags from the admin page share this vocabulary with the manifest
    parsers, so both can be merged without producing duplicates or variants
    like "TV" versus "tv".
    """
    if not isinstance(value, list):
        return []
    lowered = {str(item).strip().lower() for item in value if str(item).strip()}
    return [device for device in DEVICE_ORDER if device in lowered]


def version_identity(version: dict) -> tuple:
    """Identify one downloadable build for de-duplication across catalogs.

    Version and build number alone stop being unique once an app ships a
    separate build per device class, because those normally share a version
    number. Two rules keep both behaviours correct:

    - An explicit device tag marks a deliberate per-device build, so the URL is
      part of the identity and two device builds on one version stay apart.
    - Without a tag the entry is a single release that upstream catalogs often
      mirror at several URLs, so only version and build count and those mirrors
      keep collapsing into one entry, exactly as before.

    Unresolved entries share the placeholder version ``pending``, so they are
    always separated by URL.
    """
    name = str(version.get("version", ""))
    build = str(version.get("buildVersion", ""))
    url = str(version.get("downloadURL", ""))
    if not name or name == "pending" or not build or build == "pending":
        return (name, build, (), url)
    if normalize_devices(version.get("devices")):
        return (name, build, (), url)
    return (name, build, (), "")


def merge_device_tags(target: dict, source: dict) -> None:
    """Union two versions' device tags into ``target``."""
    merged = normalize_devices(
        list(target.get("devices") or []) + list(source.get("devices") or [])
    )
    if merged:
        target["devices"] = merged


def add_version_record(store: dict, version: dict) -> None:
    """Insert one version into ``store``, keeping the newest and unioning devices."""
    key = version_identity(version)
    url = str(version.get("downloadURL", ""))
    # The same file can arrive once tagged and once untagged, or be mirrored
    # without a tag at all. Match on the address so it is stored only once
    # instead of being published as two identical entries.
    for existing_key, (_, stored) in store.items():
        if existing_key[:2] == key[:2] and url and str(stored.get("downloadURL", "")) == url:
            key = existing_key
            break
    date = str(version.get("date", ""))
    current = store.get(key)
    if current is None:
        store[key] = (date, dict(version))
        return
    if date > current[0]:
        winner, loser = dict(version), current[1]
    else:
        winner, loser = current[1], version
    merge_device_tags(winner, loser)
    store[key] = (max(date, current[0]), winner)


def matches(filename: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(filename.lower(), pattern.lower()) for pattern in patterns)


def parse_version(tag: str, filename: str) -> tuple[str, str]:
    stem = Path(filename).stem
    # Common asset names include app_1.2.3+45.apk and app-1.2.3.ipa.
    match = re.search(r"(?<![A-Za-z])v?(\d+(?:\.\d+){1,4})(?:\+([\w.-]+))?", stem, re.I)
    if match:
        return match.group(1), match.group(2) or "1"
    return tag.lstrip("v") or "0.0.0", "1"


def accelerated_url(url: str, base: str) -> str:
    if not base:
        return url
    # GitHub proxy services expect the source URL as a readable path after the
    # proxy origin (e.g. https://proxy.example/https://github.com/owner/repo/...).
    # Percent-encoding the whole source URL breaks those services and some clients.
    return base.rstrip("/") + "/" + url


def release_versions(app: dict, defaults: dict, accelerator: str, pattern_key: str) -> list[dict]:
    """Collect release assets matching a platform's file patterns."""
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

    patterns = app.get(pattern_key, app.get("assetPatterns", ["*"]))
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
            if direct_url in seen:
                continue
            seen.add(direct_url)
            versions.append({
                "version": version,
                "buildVersion": build,
                "date": (release.get("published_at") or "")[:10] or datetime.now(timezone.utc).date().isoformat(),
                "localizedDescription": f"上游源文件：{filename}" + (f"\n\n{release['body'].strip()}" if release.get("body", "").strip() else ""),
                "downloadURL": accelerated_url(direct_url, accelerator),
                "size": asset.get("size", 0),
                "assetName": filename,
                # Direct (unaccelerated) URL for metadata parsing; stripped before output.
                "_sourceURL": direct_url,
            })
        progress.update()
    progress.finish(f"解析 {repo} Releases（{len(versions)} 个版本）")
    return versions


def latest_version_date(app: dict) -> str:
    return max((str(item.get("date", "")) for item in app.get("versions", [])), default="")


def unlink_quietly(path: str | None) -> None:
    if path:
        Path(path).unlink(missing_ok=True)


def repository_slug() -> str:
    repository = os.getenv("GITHUB_REPOSITORY", "zhanghuid/ipa.sources")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        repository = "zhanghuid/ipa.sources"
    return repository


def branch_name() -> str:
    return urllib.parse.quote(os.getenv("GITHUB_REF_NAME", "main"), safe="/")


def crc32_chunk(kind: bytes, data: bytes) -> bytes:
    body = kind + data
    return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)
