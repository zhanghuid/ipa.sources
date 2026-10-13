#!/usr/bin/env python3
"""Build the Android catalog from configured sources and submitted APK links."""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.parse
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.apk_metadata import ApkParseError, apk_metadata
from scripts.shared import (
    ROOT,
    Progress,
    accelerated_url,
    branch_name,
    classify_archive,
    download_to_tempfile,
    fetch_catalog,
    add_version_record,
    format_mismatch_message,
    latest_version_date,
    normalize_devices,
    release_versions,
    repository_slug,
    store_icon,
    unlink_quietly,
    write_json_atomic,
)

CONFIG = ROOT / "config/android/catalog.json"
SOURCES_INDEX = ROOT / "config/android/sources/index.json"
SOURCES_DIR = ROOT / "config/android/sources"
CUSTOM_ICONS_DIR = SOURCES_DIR / "custom/icons"
OUTPUT = ROOT / "android.json"
PLATFORM = "android"
PACKAGE_PATTERN = re.compile(r"[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)+")
MAX_CUSTOM_APK_SIZE = 2 * 1024 * 1024 * 1024
ICON_EXTRACTION_VERSION = "apk-icons-v1"
DEVICE_CHECK_VERSION = "manifest-v1"


def apk_metadata_for(url: str, label: str) -> dict:
    """Download a complete APK and read its manifest metadata and launcher icon."""
    temporary, downloaded = download_to_tempfile(url, label, MAX_CUSTOM_APK_SIZE, suffix=".apk")
    try:
        try:
            with zipfile.ZipFile(temporary.name) as archive:
                mismatch = format_mismatch_message(label, "apk", classify_archive(archive.namelist()))
                if mismatch:
                    raise ValueError(mismatch)
                metadata = apk_metadata(archive)
        except (zipfile.BadZipFile, ApkParseError, KeyError, OSError, RuntimeError, ValueError) as error:
            raise ValueError(f"{label}: unable to parse APK manifest: {error}") from error
    finally:
        unlink_quietly(temporary.name)
    package_name = str(metadata.get("packageName", "")).strip()
    if not PACKAGE_PATTERN.fullmatch(package_name):
        raise ValueError(f"{label}: APK has an invalid package name")
    if not metadata.get("versionName"):
        raise ValueError(f"{label}: APK has no versionName")
    metadata["size"] = downloaded
    return metadata


def hydrate_custom_catalog(catalog: dict) -> bool:
    """Resolve queued custom APK metadata and persist it into the source file."""
    changed = False
    repository = repository_slug()
    branch = branch_name()
    for app in catalog.get("apps", []):
        versions = sorted(
            app.get("versions", []),
            key=lambda item: (str(item.get("date", "")), str(item.get("version", ""))),
            reverse=True,
        )
        icon_known = bool(app.get("iconURL")) or app.get("_iconExtractionChecked") == ICON_EXTRACTION_VERSION
        devices_known = app.get("_deviceCheckVersion") == DEVICE_CHECK_VERSION
        # Manual device tags win and need no download, so merge them before the
        # parse loop; a tag-only edit must not force a full APK re-download.
        manual_devices: list[str] = []
        for item in versions:
            for device in normalize_devices(item.get("devices")):
                if device not in manual_devices:
                    manual_devices.append(device)
        if manual_devices:
            merged = manual_devices + [
                device for device in normalize_devices(app.get("devices")) if device not in manual_devices
            ]
            if normalize_devices(app.get("devices")) != merged:
                app["devices"] = merged
                changed = True
        for version in versions:
            is_latest = bool(versions) and version is versions[0]
            needs_metadata = version.get("version") == "pending" or not app.get("packageName")
            needs_icon = is_latest and not icon_known
            # A manual tag already states the devices, so no parse is needed for it.
            needs_devices = is_latest and not devices_known and not manual_devices
            if not needs_metadata and not needs_icon and not needs_devices:
                continue
            url = str(version.get("downloadURL", "")).strip()
            try:
                if not url:
                    raise ValueError("pending version has no download URL")
                metadata = apk_metadata_for(url, str(app.get("name", "APK")))
                existing = str(app.get("packageName", "")).strip()
                if existing and existing != metadata["packageName"]:
                    raise ValueError(
                        f"APK package {metadata['packageName']} does not match existing {existing}"
                    )
            except (ValueError, urllib.error.URLError, TimeoutError, OSError) as error:
                if isinstance(error, (urllib.error.URLError, TimeoutError)):
                    message = f"Unable to download APK ({type(error).__name__}); check URL access."
                else:
                    message = str(error)
                message = message[:300]
                if version.get("metadataError") != message:
                    version["metadataError"] = message
                    changed = True
                print(f"warning: unable to resolve {app.get('name', '<unnamed>')}: {message}", file=sys.stderr)
                continue
            version.pop("metadataError", None)
            app["packageName"] = metadata["packageName"]
            if metadata.get("iconPNG"):
                app["iconURL"] = store_icon(
                    CUSTOM_ICONS_DIR, metadata["packageName"], metadata["iconPNG"], repository, branch
                )
                app["_iconExtractionChecked"] = ICON_EXTRACTION_VERSION
                changed = True
            elif needs_icon:
                app["_iconExtractionChecked"] = ICON_EXTRACTION_VERSION
                changed = True
            if metadata.get("devices"):
                # Manual tags were merged above and always win, so the manifest
                # only contributes the classes the tag does not already state.
                merged = manual_devices + [
                    item for item in metadata["devices"] if item not in manual_devices
                ]
                if not manual_devices or normalize_devices(app.get("devices")) != merged:
                    app["devices"] = merged
                    changed = True
                app["_deviceCheckVersion"] = DEVICE_CHECK_VERSION
            elif needs_devices:
                app["_deviceCheckVersion"] = DEVICE_CHECK_VERSION
                changed = True
            if metadata.get("permissions"):
                app["appPermissions"] = {"permissions": metadata["permissions"], "privacy": {}}
            version.update({
                "version": metadata["versionName"],
                "buildVersion": str(metadata.get("versionCode") or "1"),
                "size": metadata["size"],
                "localizedDescription": version.get("localizedDescription", ""),
            })
            if metadata.get("minSdk") is not None:
                version["minSdk"] = metadata["minSdk"]
            changed = True
    return changed


def hydrate_release_app(app: dict, config_path: Path) -> bool:
    """Parse a tracked release APK once, caching devices/icon/package in the config.

    Returns True when the config file needs to be written back.
    """
    versions = app.get("_pendingVersions") or []
    if not versions:
        return False
    asset_name = str(versions[0].get("assetName", ""))
    source_url = str(versions[0].get("_sourceURL", ""))
    existing = (app.get("_assets") or {}).get(asset_name)
    if not source_url or (existing and app.get("_parseVersion") == ICON_EXTRACTION_VERSION):
        return False
    changed = False
    try:
        metadata = apk_metadata_for(source_url, app.get("name", "APK"))
    except (ValueError, urllib.error.URLError, TimeoutError, OSError) as error:
        message = str(error)[:300]
        if app.get("_parseError") != message:
            app["_parseError"] = message
            changed = True
        print(f"warning: unable to parse {app.get('name', '<unnamed>')}: {message}", file=sys.stderr)
        return changed
    app.pop("_parseError", None)
    app["packageName"] = metadata["packageName"]
    app["devices"] = metadata.get("devices") or ["phone", "pad"]
    if metadata.get("iconPNG"):
        app["iconURL"] = store_icon(
            CUSTOM_ICONS_DIR, metadata["packageName"], metadata["iconPNG"], repository_slug(), branch_name()
        )
    # Manifest values are per asset: record them so each version keeps its own
    # versionCode and minSdk instead of inheriting the newest build's numbers.
    assets = dict(app.get("_assets") or {})
    record: dict = {"url": source_url}
    if metadata.get("versionCode"):
        record["versionCode"] = str(metadata["versionCode"])
    if metadata.get("minSdk") is not None:
        record["minSdk"] = metadata["minSdk"]
    assets[asset_name] = record
    app["_assets"] = assets
    app["_parseVersion"] = ICON_EXTRACTION_VERSION
    _ = config_path
    return True


def merge_catalogs(named_catalogs: list[tuple[str, dict]], accelerator: str, default_devices: list[str]) -> list[dict]:
    """Merge Android catalogs by package name, retaining the newest duplicate version."""
    grouped: dict[str, list[tuple[int, dict]]] = {}
    order: list[str] = []
    for source_index, (_source_name, catalog) in enumerate(named_catalogs):
        for app in catalog.get("apps", []):
            package_name = str(app.get("packageName") or app.get("bundleIdentifier") or "").strip()
            if not package_name:
                package_name = "name:" + str(app.get("name", "")).strip().casefold()
            if package_name == "name:":
                continue
            app.setdefault("packageName", package_name)
            if package_name not in grouped:
                grouped[package_name] = []
                order.append(package_name)
            grouped[package_name].append((source_index, app))

    merged: list[dict] = []
    for package_name in order:
        candidates = grouped[package_name]
        candidates.sort(key=lambda row: (latest_version_date(row[1]), -row[0]), reverse=True)
        result = {key: value for key, value in candidates[0][1].items() if not key.startswith("_")}
        versions_by_key: dict[tuple[str, str, str], tuple[str, dict]] = {}
        for _, app in candidates:
            for version in app.get("versions", []):
                add_version_record(versions_by_key, version)
        versions = [value[1] for value in versions_by_key.values()]
        versions.sort(key=lambda item: (str(item.get("date", "")), str(item.get("version", ""))), reverse=True)
        # A failed metadata fetch is a retry signal recorded in the source file; it
        # must not reach the published catalog.
        for version in versions:
            version.pop("metadataError", None)
        if accelerator:
            for version in versions:
                download_url = str(version.get("downloadURL", ""))
                parsed = urllib.parse.urlparse(download_url)
                if parsed.hostname == "github.com" and "/releases/download/" in parsed.path:
                    version["downloadURL"] = accelerated_url(download_url, accelerator)
        result["versions"] = versions
        devices: list[str] = []
        # Per-address tags describe the app as a whole, because one app can now
        # ship a phone build and a TV build as separate addresses.
        for version in versions:
            for device in normalize_devices(version.get("devices")):
                if device not in devices:
                    devices.append(device)
        for _, app in candidates:
            for device in app.get("devices", []) or []:
                if device not in devices:
                    devices.append(device)
        result["platform"] = PLATFORM
        # Explicit tags replace the defaults; only a device-less app falls back.
        result["devices"] = normalize_devices(devices) or list(default_devices)
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

    remote_sources: list[dict] = []
    if SOURCES_INDEX.exists():
        remote_sources = json.loads(SOURCES_INDEX.read_text(encoding="utf-8")).get("sources", [])
    source_progress = Progress("获取 Android 源目录", len(remote_sources) + len(config.get("apps", [])))
    source_progress.start()
    fetched_sources: list[tuple[dict, dict]] = []
    for remote in remote_sources:
        name = remote.get("name", remote["url"])
        print(f"Fetching catalog: {name}")
        source_catalog = fetch_catalog(remote["url"], name)
        fetched_sources.append((remote, source_catalog))
        input_catalogs.append((name, source_catalog))
        source_progress.update()
    for remote, source_catalog in fetched_sources:
        write_json_atomic(SOURCES_DIR / remote["file"], source_catalog)

    custom_catalog_path = SOURCES_DIR / "custom/apks.json"
    if custom_catalog_path.exists():
        custom_catalog = json.loads(custom_catalog_path.read_text(encoding="utf-8"))
        if not isinstance(custom_catalog.get("apps"), list):
            raise ValueError(f"Custom catalog at {custom_catalog_path} has no apps array")
        if hydrate_custom_catalog(custom_catalog):
            write_json_atomic(custom_catalog_path, custom_catalog)
            print(f"Updated custom APK metadata in {custom_catalog_path}")
        ready_custom_catalog = {
            **custom_catalog,
            "apps": [
                {
                    **app,
                    "versions": [version for version in app.get("versions", []) if version.get("version") != "pending"],
                }
                for app in custom_catalog["apps"]
                if app.get("packageName")
                and any(version.get("version") != "pending" for version in app.get("versions", []))
            ],
        }
        pending_count = sum(
            version.get("version") == "pending"
            for app in custom_catalog["apps"]
            for version in app.get("versions", [])
        )
        if pending_count:
            print(f"warning: {pending_count} custom APK version(s) are pending metadata and were skipped for this catalog run")
        input_catalogs.append(("custom/apks.json", ready_custom_catalog))

    config_changed = False
    for app in config.get("apps", []):
        if not app.get("repo"):
            raise ValueError(f"App {app.get('name', '<unnamed>')} has no repo")
        versions = release_versions(app, defaults, accelerator, "assetPatterns")
        if not versions:
            print(f"warning: no matching APK assets for {app['repo']}", file=sys.stderr)
            continue
        latest_asset = str((versions[0] if versions else {}).get("assetName", ""))
        known_assets = app.get("_assets") or {}
        if (
            not app.get("packageName")
            or not app.get("devices")
            or app.get("_parseVersion") != ICON_EXTRACTION_VERSION
            or latest_asset not in known_assets
        ):
            # Tracked releases are parsed once so the manifest-derived devices and
            # launcher icon end up in the config instead of being guessed.
            app["_pendingVersions"] = sorted(
                versions, key=lambda item: (str(item.get("date", "")), str(item.get("version", ""))), reverse=True
            )
            if hydrate_release_app(app, CONFIG):
                config_changed = True
            app.pop("_pendingVersions", None)
        versions = [{key: value for key, value in version.items() if not key.startswith("_")} for version in versions]
        assets = app.get("_assets") or {}
        for version in versions:
            known = assets.get(str(version.get("assetName", "")))
            if known:
                version["buildVersion"] = known.get("versionCode", version["buildVersion"])
                if known.get("minSdk") is not None:
                    version["minSdk"] = known["minSdk"]
        input_catalogs.append((f"GitHub Releases: {app['repo']}", {"apps": [{
            "name": app["name"],
            "packageName": app["packageName"],
            "developerName": app.get("developerName", defaults.get("developerName", "Upstream")),
            "subtitle": app.get("subtitle", "APK"),
            "localizedDescription": app.get("localizedDescription", "Automatically synced from GitHub Releases."),
            "upstream": {
                "repo": app["repo"],
                "assetPatterns": app.get("assetPatterns", ["*.apk"]),
                "excludePatterns": app.get("excludePatterns", []),
            },
            "iconURL": app.get("iconURL", ""),
            "category": app.get("category", defaults.get("category", "utilities")),
            "devices": app.get("devices", []),
            "versions": versions,
            "appPermissions": {"permissions": [], "privacy": {}},
        }]}))
        source_progress.update()
    source_progress.finish("获取 Android 源目录完成")

    if config_changed:
        write_json_atomic(CONFIG, config)
        print(f"Updated tracked APK metadata in {CONFIG}")

    total_apps = sum(len(item.get("apps", [])) for _, item in input_catalogs)
    merge_progress = Progress("合并并去重 Android 应用和版本", max(1, total_apps))
    merge_progress.start()
    catalog["apps"] = merge_catalogs(input_catalogs, accelerator, default_devices)
    for app in catalog["apps"]:
        override = overrides.get(app.get("packageName", ""))
        if override:
            app["devices"] = [str(item) for item in override]
    merge_progress.update(len(catalog["apps"]))
    merge_progress.finish(f"合并完成（{len(catalog['apps'])} 个应用）")
    write_json_atomic(OUTPUT, catalog)
    print(f"Wrote {len(catalog['apps'])} Android apps to {OUTPUT}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (urllib.error.URLError, urllib.error.HTTPError, ValueError, KeyError) as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(1)
