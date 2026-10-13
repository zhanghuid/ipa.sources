#!/usr/bin/env python3
"""Build the iOS/tvOS catalog from configured sources and submitted IPA links."""

from __future__ import annotations

import json
import os
import plistlib
import re
import struct
import sys
import urllib.error
import urllib.parse
import zipfile
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.shared import (
    ROOT,
    Progress,
    accelerated_url,
    branch_name,
    download_to_tempfile,
    fetch_catalog,
    latest_version_date,
    release_versions,
    repository_slug,
    store_icon,
    unlink_quietly,
    write_json_atomic,
)

CONFIG = ROOT / "config/ios/catalog.json"
SOURCES_INDEX = ROOT / "config/ios/sources/index.json"
SOURCES_DIR = ROOT / "config/ios/sources"
CUSTOM_ICONS_DIR = SOURCES_DIR / "custom/icons"
OUTPUT = ROOT / "apps.json"
PLATFORM = "ios"
MAX_CUSTOM_IPA_SIZE = 2 * 1024 * 1024 * 1024
ICON_EXTRACTION_VERSION = "cgbi-v2"
DEVICE_CHECK_VERSION = "family-v1"
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"

# UIDeviceFamily values from Apple's Info.plist keys.
DEVICE_FAMILIES = {1: "phone", 2: "pad", 3: "tv", 4: "watch", 6: "mac", 7: "vision"}


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


def device_families(info: dict) -> list[str]:
    """Map Info.plist UIDeviceFamily values to the shared device vocabulary."""
    raw = info.get("UIDeviceFamily") or []
    devices: list[str] = []
    for value in raw:
        try:
            family = int(value)
        except (TypeError, ValueError):
            continue
        name = DEVICE_FAMILIES.get(family)
        if name and name not in devices:
            devices.append(name)
    return devices


def ipa_metadata(url: str, label: str) -> dict:
    """Download a complete IPA and read its main Payload app Info.plist."""
    temporary, downloaded = download_to_tempfile(url, label, MAX_CUSTOM_IPA_SIZE, suffix=".ipa")
    try:
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
    finally:
        unlink_quietly(temporary.name)

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
        "devices": device_families(info),
        "size": downloaded,
        "iconPNG": icon_png,
    }


def app_store_metadata(bundle_id: str) -> dict:
    """Best-effort metadata lookup for artwork and developer details."""
    query = urllib.parse.urlencode({"bundleId": bundle_id, "country": "cn"})
    request = urllib.request.Request(
        f"https://itunes.apple.com/lookup?{query}",
        headers={"User-Agent": "personal-app-source"},
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
    repository = repository_slug()
    branch = branch_name()
    for app in catalog.get("apps", []):
        # Normalise icon URLs written before the platform directories were split.
        icon_url = str(app.get("iconURL", ""))
        if icon_url and "/sources/custom/icons/" in icon_url:
            identifier = icon_url.rsplit("/", 1)[-1]
            expected = (
                f"https://raw.githubusercontent.com/{repository}/{branch}/"
                f"{CUSTOM_ICONS_DIR.relative_to(ROOT).as_posix()}/{identifier}"
            )
            if icon_url != expected:
                app["iconURL"] = expected
                changed = True
        versions = sorted(
            app.get("versions", []),
            key=lambda item: (str(item.get("date", "")), str(item.get("version", ""))),
            reverse=True,
        )
        icon_known = bool(app.get("iconURL")) or app.get("_iconExtractionChecked") == ICON_EXTRACTION_VERSION
        devices_known = app.get("_deviceCheckVersion") == DEVICE_CHECK_VERSION
        if app.get("bundleIdentifier") and not icon_known:
            store_metadata = app_store_metadata(str(app["bundleIdentifier"]))
            app.update({key: value for key, value in store_metadata.items() if value})
            if app.get("iconURL"):
                app["_iconExtractionChecked"] = ICON_EXTRACTION_VERSION
                changed = True
        for version in versions:
            is_latest = bool(versions) and version is versions[0]
            needs_metadata = version.get("version") == "pending" or not app.get("bundleIdentifier")
            needs_icon = is_latest and not icon_known
            needs_devices = is_latest and not devices_known
            if not needs_metadata and not needs_icon and not needs_devices:
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
                if isinstance(error, (urllib.error.URLError, TimeoutError)):
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
                app["iconURL"] = store_icon(
                    CUSTOM_ICONS_DIR, metadata["bundleIdentifier"], metadata["iconPNG"], repository, branch
                )
                app["_iconExtractionChecked"] = ICON_EXTRACTION_VERSION
                changed = True
            elif needs_icon:
                app["_iconExtractionChecked"] = ICON_EXTRACTION_VERSION
                changed = True
            if metadata["devices"]:
                app["devices"] = metadata["devices"]
                app["_deviceCheckVersion"] = DEVICE_CHECK_VERSION
                changed = True
            elif needs_devices:
                app["_deviceCheckVersion"] = DEVICE_CHECK_VERSION
                changed = True
            version.update({
                "version": metadata["version"],
                "buildVersion": metadata["buildVersion"],
                "size": metadata["size"],
                "minOSVersion": metadata["minOSVersion"],
                "localizedDescription": version.get("localizedDescription", ""),
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


def merge_catalogs(named_catalogs: list[tuple[str, dict]], accelerator: str, default_devices: list[str]) -> list[dict]:
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
        devices: list[str] = []
        for _, app, _ in candidates:
            for device in app.get("devices", []) or []:
                if device not in devices:
                    devices.append(device)
        result["platform"] = PLATFORM
        result["devices"] = devices or list(default_devices)
        merged.append(result)
    return merged


def main() -> int:
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    accelerator = (os.getenv("ACCELERATOR_BASE_URL") or config.get("acceleratorBaseURL", "")).strip()
    defaults = config.get("defaults", {})
    default_devices = [str(item) for item in config.get("defaultDevices", ["phone", "pad"])]
    overrides = config.get("deviceOverrides", {})
    source = dict(config.get("source", {}))
    catalog = {**source, "platform": PLATFORM, "apps": []}
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
        write_json_atomic(SOURCES_DIR / remote["file"], source_catalog)

    custom_catalog_path = SOURCES_DIR / "custom/ipas.json"
    if custom_catalog_path.exists():
        custom_catalog = json.loads(custom_catalog_path.read_text(encoding="utf-8"))
        if not isinstance(custom_catalog.get("apps"), list):
            raise ValueError(f"Custom catalog at {custom_catalog_path} has no apps array")
        if hydrate_custom_catalog(custom_catalog):
            write_json_atomic(custom_catalog_path, custom_catalog)
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
        versions = release_versions(app, defaults, accelerator, "assetPatterns")
        if not versions:
            print(f"warning: no matching assets for {app['repo']}", file=sys.stderr)
            continue
        for version in versions:
            version["minOSVersion"] = app.get("minOSVersion", "14.0")
        input_catalogs.append((f"GitHub Releases: {app['repo']}", {"apps": [{
            "name": app["name"],
            "bundleIdentifier": app["bundleIdentifier"],
            "developerName": app.get("developerName", defaults.get("developerName", "Upstream")),
            "subtitle": app.get("subtitle", "IPA"),
            "localizedDescription": app.get("localizedDescription", "Automatically synced from GitHub Releases."),
            "upstream": {"repo": app["repo"], "assetPatterns": app.get("assetPatterns", ["*.ipa"]), "excludePatterns": app.get("excludePatterns", [])},
            "iconURL": app.get("iconURL", ""),
            "category": app.get("category", defaults.get("category", "utilities")),
            "devices": app.get("devices", []),
            "versions": versions,
            "appPermissions": {"entitlements": [], "privacy": {}},
        }]}))
        source_progress.update()
    source_progress.finish("获取源目录完成")

    total_apps = sum(len(item.get("apps", [])) for _, item in input_catalogs)
    merge_progress = Progress("合并并去重应用和版本", max(1, total_apps))
    merge_progress.start()
    catalog["apps"] = merge_catalogs(input_catalogs, accelerator, default_devices)
    for app in catalog["apps"]:
        override = overrides.get(app.get("bundleIdentifier", ""))
        if override:
            app["devices"] = [str(item) for item in override]
    merge_progress.update(len(catalog["apps"]))
    merge_progress.finish(f"合并完成（{len(catalog['apps'])} 个应用）")
    write_json_atomic(OUTPUT, catalog)
    print(f"Wrote {len(catalog['apps'])} iOS apps to {OUTPUT}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (urllib.error.URLError, urllib.error.HTTPError, ValueError, KeyError) as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(1)
