#!/usr/bin/env python3
"""Build an AltSource-compatible catalog from configured GitHub releases."""

from __future__ import annotations

import fnmatch
import json
import os
import plistlib
import re
import sys
import struct
import tempfile
import time
import threading
import urllib.error
import urllib.parse
import urllib.request
import zipfile
import zlib
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config/apps.json"
SOURCES_INDEX = ROOT / "config/sources/index.json"
SOURCES_DIR = ROOT / "config/sources"
OUTPUT = ROOT / "apps.json"
MAX_CUSTOM_IPA_SIZE = 2 * 1024 * 1024 * 1024
CUSTOM_ICONS_DIR = SOURCES_DIR / "custom/icons"
ICON_EXTRACTION_VERSION = "cgbi-v1"
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def png_chunks(image: bytes) -> list[tuple[bytes, bytes]]:
    if not image.startswith(PNG_SIGNATURE):
        raise ValueError("icon is not a PNG image")
    chunks: list[tuple[bytes, bytes]] = []
    offset = len(PNG_SIGNATURE)
    while offset + 12 <= len(image):
        length = struct.unpack_from(">I", image, offset)[0]
        end = offset + 12 + length
        if end > len(image):
            raise ValueError("PNG chunk exceeds file size")
        kind = image[offset + 4:offset + 8]
        chunks.append((kind, image[offset + 8:offset + 8 + length]))
        offset = end
        if kind == b"IEND":
            break
    return chunks


def png_dimensions(image: bytes) -> tuple[int, int]:
    for kind, data in png_chunks(image):
        if kind == b"IHDR" and len(data) == 13:
            return struct.unpack_from(">II", data, 0)
    raise ValueError("PNG has no valid IHDR chunk")


def normalize_cgbi_png(image: bytes) -> bytes:
    """Convert Apple's CgBI PNG variant to a standards-compliant PNG."""
    chunks = png_chunks(image)
    if not any(kind == b"CgBI" for kind, _ in chunks):
        return image
    header = next((data for kind, data in chunks if kind == b"IHDR"), None)
    if header is None or len(header) != 13:
        raise ValueError("CgBI image has no valid IHDR chunk")
    width, height, depth, color_type, compression, filter_method, interlace = struct.unpack(">IIBBBBB", header)
    if depth != 8 or color_type not in (2, 6) or interlace != 0 or compression != 0 or filter_method != 0:
        raise ValueError("unsupported CgBI icon encoding")
    channels = 4 if color_type == 6 else 3
    stride = width * channels
    if width < 1 or height < 1 or stride * height > 64 * 1024 * 1024:
        raise ValueError("CgBI icon dimensions exceed processing limits")
    compressed = b"".join(data for kind, data in chunks if kind == b"IDAT")
    raw = zlib.decompress(compressed, -15)
    if len(raw) != height * (stride + 1):
        raise ValueError("CgBI pixel data length does not match its dimensions")

    def paeth(left: int, above: int, upper_left: int) -> int:
        estimate = left + above - upper_left
        distances = (abs(estimate - left), abs(estimate - above), abs(estimate - upper_left))
        return (left, above, upper_left)[distances.index(min(distances))]

    rows: list[bytearray] = []
    offset = 0
    previous = bytearray(stride)
    for _ in range(height):
        filter_type = raw[offset]
        row = bytearray(raw[offset + 1:offset + 1 + stride])
        offset += stride + 1
        if filter_type > 4:
            raise ValueError("CgBI icon uses an invalid PNG row filter")
        for index in range(stride):
            left = row[index - channels] if index >= channels else 0
            above = previous[index]
            upper_left = previous[index - channels] if index >= channels else 0
            if filter_type == 1:
                predictor = left
            elif filter_type == 2:
                predictor = above
            elif filter_type == 3:
                predictor = (left + above) // 2
            elif filter_type == 4:
                predictor = paeth(left, above, upper_left)
            else:
                predictor = 0
            row[index] = (row[index] + predictor) & 0xFF
        rows.append(row)
        previous = row

    standard_scanlines = bytearray()
    for row in rows:
        standard_scanlines.append(0)
        for x in range(0, stride, channels):
            blue, green, red = row[x:x + 3]
            if channels == 4:
                alpha = row[x + 3]
                if alpha == 0:
                    red = green = blue = 0
                elif alpha < 255:
                    red = min(255, (red * 255 + alpha // 2) // alpha)
                    green = min(255, (green * 255 + alpha // 2) // alpha)
                    blue = min(255, (blue * 255 + alpha // 2) // alpha)
                standard_scanlines.extend((red, green, blue, alpha))
            else:
                standard_scanlines.extend((red, green, blue))

    def make_chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    return (
        PNG_SIGNATURE
        + make_chunk(b"IHDR", header)
        + make_chunk(b"IDAT", zlib.compress(standard_scanlines))
        + make_chunk(b"IEND", b"")
    )


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


def fetch_catalog(url: str, label: str) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": "personal-ipa-source"})
    head_request = urllib.request.Request(url, headers={"User-Agent": "personal-ipa-source"}, method="HEAD")
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


def extract_icon_png(archive: zipfile.ZipFile, app_root: str, info: dict, names: list[str]) -> bytes | None:
    """Return the largest declared primary icon PNG stored directly in the app bundle."""
    icon_names: list[str] = []
    for key in ("CFBundleIcons", "CFBundleIcons~ipad"):
        primary = info.get(key, {}).get("CFBundlePrimaryIcon", {})
        icon_names.extend(str(name) for name in primary.get("CFBundleIconFiles", []))
        icon_name = primary.get("CFBundleIconName")
        if icon_name:
            icon_names.append(str(icon_name))
    icon_names = [name.rsplit("/", 1)[-1].removesuffix(".png") for name in icon_names]

    candidates: list[tuple[int, bytes]] = []
    prefix = app_root.rstrip("/") + "/"
    for path in names:
        if not path.startswith(prefix) or "/" in path[len(prefix):] or not path.lower().endswith(".png"):
            continue
        basename = path[len(prefix):]
        stem = basename[:-4]
        declared_match = any(
            stem == name or stem.startswith(name + "@") or stem.startswith(name + "~")
            for name in icon_names
        )
        if not declared_match and not stem.lower().startswith("appicon"):
            continue
        try:
            image_data = archive.read(path)
            width, height = png_dimensions(image_data)
            if width < 1 or height < 1 or width * height > 16_000_000:
                continue
            candidates.append((width * height, normalize_cgbi_png(image_data)))
        except (KeyError, OSError, ValueError, zipfile.BadZipFile, zlib.error, RuntimeError):
            continue
    return max(candidates, key=lambda candidate: candidate[0])[1] if candidates else None


def store_custom_icon(bundle_id: str, image: bytes) -> str:
    """Save an extracted icon as a public raw GitHub asset and return its URL."""
    if not re.fullmatch(r"[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+", bundle_id):
        raise ValueError("refusing to store an icon with an invalid bundle ID")
    if len(image) > 12 * 1024 * 1024:
        raise ValueError(f"{bundle_id}: extracted icon exceeds the 12 MiB limit")
    repository = os.getenv("GITHUB_REPOSITORY", "zhanghuid/ipa.sources")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        repository = "zhanghuid/ipa.sources"
    branch = urllib.parse.quote(os.getenv("GITHUB_REF_NAME", "main"), safe="/")
    CUSTOM_ICONS_DIR.mkdir(parents=True, exist_ok=True)
    target = CUSTOM_ICONS_DIR / f"{bundle_id}.png"
    if not target.exists() or target.read_bytes() != image:
        temporary = target.with_suffix(".png.tmp")
        temporary.write_bytes(image)
        temporary.replace(target)
    return f"https://raw.githubusercontent.com/{repository}/{branch}/config/sources/custom/icons/{bundle_id}.png"


def ipa_metadata(url: str, label: str) -> dict:
    """Download a complete IPA and read its main Payload app Info.plist."""
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https":
        raise ValueError(f"{label}: IPA download URL must use HTTPS")
    request = urllib.request.Request(url, headers={"User-Agent": "personal-ipa-source"})
    with tempfile.NamedTemporaryFile(prefix="ipa-source-", suffix=".ipa") as temporary:
        progress = Progress(f"下载 IPA：{label}", 100)
        progress.start()
        downloaded = 0
        content_length = 0
        download_complete = False
        try:
            with urllib.request.urlopen(request, timeout=180) as response:
                if urllib.parse.urlparse(response.geturl()).scheme != "https":
                    raise ValueError(f"{label}: IPA download redirected to a non-HTTPS URL")
                content_length = int(response.headers.get("Content-Length", "0") or 0)
                if content_length > MAX_CUSTOM_IPA_SIZE:
                    raise ValueError(f"{label}: IPA exceeds the 2 GiB processing limit")
                if content_length:
                    progress.total = content_length
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    downloaded += len(chunk)
                    if downloaded > MAX_CUSTOM_IPA_SIZE:
                        raise ValueError(f"{label}: IPA exceeds the 2 GiB processing limit")
                    temporary.write(chunk)
                    if content_length:
                        progress.update(min(downloaded, content_length), content_length)
                    elif downloaded % (16 * 1024 * 1024) < 1024 * 1024:
                        print(f"  {label}: 已下载 {downloaded // (1024 * 1024)} MiB", file=sys.stderr, flush=True)
            download_complete = True
        finally:
            outcome = "下载完成" if download_complete else "下载中断"
            progress.finish(f"{outcome}：{label}（{downloaded / (1024 * 1024):.1f} MiB）")
        temporary.flush()
        try:
            with zipfile.ZipFile(temporary.name) as archive:
                names = archive.namelist()
                plist_names = [
                    name for name in names
                    if re.fullmatch(r"Payload/[^/]+\.app/Info\.plist", name)
                ]
                if not plist_names:
                    raise ValueError(f"{label}: IPA does not contain a main app Info.plist")
                info_path = max(plist_names, key=lambda name: name.count("/"))
                info_entry = archive.getinfo(info_path)
                if info_entry.file_size > 8 * 1024 * 1024:
                    raise ValueError(f"{label}: Info.plist exceeds the 8 MiB processing limit")
                info = plistlib.loads(archive.read(info_path))
                icon_png = extract_icon_png(archive, info_path.rsplit("/", 1)[0], info, names)
        except (
            zipfile.BadZipFile,
            KeyError,
            plistlib.InvalidFileException,
            zlib.error,
            EOFError,
            RuntimeError,
            NotImplementedError,
            ValueError,
        ) as error:
            raise ValueError(f"{label}: unable to parse IPA Info.plist: {error}") from error

    bundle_id = str(info.get("CFBundleIdentifier", "")).strip()
    version = str(info.get("CFBundleShortVersionString", "")).strip()
    if not re.fullmatch(r"[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+", bundle_id):
        raise ValueError(f"{label}: IPA has an invalid CFBundleIdentifier")
    if not version:
        raise ValueError(f"{label}: IPA has no CFBundleShortVersionString")
    return {
        "bundleIdentifier": bundle_id,
        "version": version,
        "buildVersion": str(info.get("CFBundleVersion", "1")),
        "minOSVersion": str(info.get("MinimumOSVersion", "14.0")),
        "size": downloaded,
        "iconPNG": icon_png,
    }


def app_store_metadata(bundle_id: str) -> dict:
    """Best-effort metadata lookup for artwork and developer details."""
    query = urllib.parse.urlencode({"bundleId": bundle_id, "country": "cn"})
    request = urllib.request.Request(
        f"https://itunes.apple.com/lookup?{query}",
        headers={"User-Agent": "personal-ipa-source"},
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            result = json.load(response).get("results", [])
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
        return {}
    if not result:
        return {}
    app = result[0]
    return {
        "developerName": app.get("sellerName", ""),
        "subtitle": app.get("trackName", ""),
        "localizedDescription": app.get("description", ""),
        "iconURL": app.get("artworkUrl512") or app.get("artworkUrl100", ""),
        "category": "games" if str(app.get("primaryGenreName", "")).lower() == "games" else "utilities",
    }


def hydrate_custom_catalog(catalog: dict) -> bool:
    """Resolve queued custom IPA metadata and persist it into the source file."""
    changed = False
    for app in catalog.get("apps", []):
        versions = sorted(
            app.get("versions", []),
            key=lambda item: (str(item.get("date", "")), str(item.get("version", ""))),
            reverse=True,
        )
        if app.get("bundleIdentifier") and not app.get("iconURL") and app.get("_iconExtractionChecked") != ICON_EXTRACTION_VERSION:
            store_metadata = app_store_metadata(str(app["bundleIdentifier"]))
            app.update({key: value for key, value in store_metadata.items() if value})
            if app.get("iconURL"):
                app["_iconExtractionChecked"] = ICON_EXTRACTION_VERSION
                changed = True
        for version in versions:
            is_latest = bool(versions) and version is versions[0]
            needs_metadata = version.get("version") == "pending" or not app.get("bundleIdentifier")
            needs_icon = is_latest and not app.get("iconURL") and app.get("_iconExtractionChecked") != ICON_EXTRACTION_VERSION
            if not needs_metadata and not needs_icon:
                continue
            url = str(version.get("downloadURL", "")).strip()
            try:
                if not url:
                    raise ValueError("pending version has no download URL")
                metadata = ipa_metadata(url, str(app.get("name", "IPA")))
                existing_bundle = str(app.get("bundleIdentifier", "")).strip()
                if existing_bundle and existing_bundle != metadata["bundleIdentifier"]:
                    raise ValueError(
                        f"IPA Bundle ID {metadata['bundleIdentifier']} does not match existing {existing_bundle}"
                    )
            except (ValueError, urllib.error.URLError, TimeoutError, OSError) as error:
                if isinstance(error, urllib.error.URLError) or isinstance(error, TimeoutError):
                    message = f"Unable to download IPA ({type(error).__name__}); check URL access."
                else:
                    message = str(error)
                message = message[:300]
                if version.get("metadataError") != message:
                    version["metadataError"] = message
                    changed = True
                print(f"warning: unable to resolve {app.get('name', '<unnamed>')}: {message}", file=sys.stderr)
                continue
            version.pop("metadataError", None)
            app["bundleIdentifier"] = metadata["bundleIdentifier"]
            app.update({
                key: value for key, value in app_store_metadata(metadata["bundleIdentifier"]).items()
                if value
            })
            if metadata["iconPNG"]:
                app["iconURL"] = store_custom_icon(metadata["bundleIdentifier"], metadata["iconPNG"])
                app["_iconExtractionChecked"] = ICON_EXTRACTION_VERSION
                changed = True
            elif needs_icon:
                app["_iconExtractionChecked"] = ICON_EXTRACTION_VERSION
                changed = True
            version.update({
                "version": metadata["version"],
                "buildVersion": metadata["buildVersion"],
                "size": metadata["size"],
                "minOSVersion": metadata["minOSVersion"],
                "localizedDescription": "",
            })
            changed = True
    grouped: dict[str, dict] = {}
    unresolved_apps: list[dict] = []
    for app in catalog["apps"]:
        bundle_id = str(app.get("bundleIdentifier", "")).strip()
        if not bundle_id:
            unresolved_apps.append(app)
            continue
        current = grouped.get(bundle_id)
        if current is None:
            grouped[bundle_id] = app
            continue
        versions_by_key: dict[tuple[str, str], dict] = {
            (str(item.get("version", "")), str(item.get("buildVersion", ""))): item
            for item in current.get("versions", [])
        }
        for item in app.get("versions", []):
            key = (str(item.get("version", "")), str(item.get("buildVersion", "")))
            prior = versions_by_key.get(key)
            if prior is None or str(item.get("date", "")) > str(prior.get("date", "")):
                versions_by_key[key] = item
        current["versions"] = sorted(
            versions_by_key.values(),
            key=lambda item: (str(item.get("date", "")), str(item.get("version", ""))),
            reverse=True,
        )
        changed = True
    catalog["apps"] = [*grouped.values(), *unresolved_apps]
    return changed


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
        result = {key: value for key, value in candidates[0][1].items() if not key.startswith("_")}
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
    source_index = json.loads(SOURCES_INDEX.read_text(encoding="utf-8"))
    remote_sources = source_index.get("sources", [])
    source_progress = Progress("获取源目录", len(remote_sources) + len(config.get("apps", [])))
    source_progress.start()
    fetched_sources: list[tuple[dict, dict]] = []
    for remote in remote_sources:
        name = remote.get("name", remote["url"])
        print(f"Fetching catalog: {name}")
        source_catalog = fetch_catalog(remote["url"], name)
        fetched_sources.append((remote, source_catalog))
        input_catalogs.append((name, source_catalog))
        source_progress.update()

    # Save the upstream catalogs as repository-scoped inputs only after all fetches
    # succeed, so a temporary outage cannot leave a partially refreshed source set.
    for remote, source_catalog in fetched_sources:
        source_path = SOURCES_DIR / remote["file"]
        source_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = source_path.with_suffix(source_path.suffix + ".tmp")
        temporary_path.write_text(json.dumps(source_catalog, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary_path.replace(source_path)
    custom_catalog_path = SOURCES_DIR / "custom/ipas.json"
    if custom_catalog_path.exists():
        custom_catalog = json.loads(custom_catalog_path.read_text(encoding="utf-8"))
        if not isinstance(custom_catalog.get("apps"), list):
            raise ValueError(f"Custom catalog at {custom_catalog_path} has no apps array")
        if hydrate_custom_catalog(custom_catalog):
            custom_catalog_path.write_text(
                json.dumps(custom_catalog, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            print(f"Updated custom IPA metadata in {custom_catalog_path}")
        ready_custom_catalog = {
            **custom_catalog,
            "apps": [
                {
                    **app,
                    "versions": [version for version in app.get("versions", []) if version.get("version") != "pending"],
                }
                for app in custom_catalog["apps"]
                if app.get("bundleIdentifier")
                and any(version.get("version") != "pending" for version in app.get("versions", []))
            ],
        }
        pending_count = sum(
            version.get("version") == "pending"
            for app in custom_catalog["apps"]
            for version in app.get("versions", [])
        )
        if pending_count:
            print(f"warning: {pending_count} custom IPA version(s) are pending metadata and were skipped for this catalog run")
        input_catalogs.append(("custom/ipas.json", ready_custom_catalog))
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
