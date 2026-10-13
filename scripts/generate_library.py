#!/usr/bin/env python3
"""Publish the subscription sources in ``config/library/*.json`` for clients.

The stored files are the source of truth and carry internal bookkeeping
(``failStreak``, ``error``, probe timings). This script projects them into
stable, importable artifacts under ``sources/``:

* ``sources/index.json``     every source, grouped by category
* ``sources/<category>.json`` one file per category
* ``sources/available.json`` only sources that answered on the last probe

Nothing is merged physically: a client imports one of these URLs and reads the
list itself. Unavailable sources stay in ``index.json`` and the per-category
files, marked with their status, so one bad probe never silently drops a source
from a subscription.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.shared import ROOT, LIBRARY_CATEGORIES, write_json_atomic

LIBRARY_DIR = ROOT / "config/library"
OUTPUT_DIR = ROOT / "sources"

# Fields a client may see. Internal probe bookkeeping stays in config/library.
PUBLIC_FIELDS = ("title", "url", "status", "lastCheckedAt", "httpStatus", "note")


def public_entry(entry: dict) -> dict:
    published: dict = {}
    for field in PUBLIC_FIELDS:
        value = entry.get(field)
        if value in (None, ""):
            continue
        published[field] = value
    return published


def entry_status(entry: dict) -> str:
    if entry.get("enabled") is False:
        return "disabled"
    status = str(entry.get("status") or "unchecked")
    return status


def load_category(category: str, label: str) -> dict:
    path = LIBRARY_DIR / f"{category}.json"
    if not path.exists():
        return {"name": label, "sources": []}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data.get("sources"), list):
        raise ValueError(f"{path} 缺少 sources 数组")
    return data


def build() -> int:
    generated_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    index_categories = []
    available: list[dict] = []
    total = 0

    for category, label in LIBRARY_CATEGORIES:
        data = load_category(category, label)
        entries = []
        usable = 0
        for entry in data["sources"]:
            status = entry_status(entry)
            published = public_entry(entry)
            published["status"] = status
            entries.append(published)
            if status == "available":
                usable += 1
                available.append({"category": category, "categoryName": label, **published})
        total += len(entries)
        write_json_atomic(
            OUTPUT_DIR / f"{category}.json",
            {
                "name": data.get("name") or label,
                "category": category,
                "generatedAt": generated_at,
                "total": len(entries),
                "sources": entries,
            },
        )
        index_categories.append(
            {
                "id": category,
                "name": label,
                "file": f"{category}.json",
                "total": len(entries),
                "available": usable,
            }
        )
        print(f"{label}（{category}）：{len(entries)} 条，其中可用 {usable} 条")

    write_json_atomic(
        OUTPUT_DIR / "index.json",
        {"generatedAt": generated_at, "categories": index_categories},
    )
    write_json_atomic(
        OUTPUT_DIR / "available.json",
        {"generatedAt": generated_at, "total": len(available), "sources": available},
    )
    print(f"Wrote {total} sources to {OUTPUT_DIR}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(build())
    except (ValueError, KeyError) as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(1)
