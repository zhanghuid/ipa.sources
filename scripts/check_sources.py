#!/usr/bin/env python3
"""Check whether the subscription sources in ``config/library/*.json`` respond.

Availability is decided by the HTTP status code alone: any 2xx/3xx counts as
available and everything else (4xx, 5xx, timeouts, connection errors) counts as
a failure. No content is inspected, so a page that answers 200 with an error or
login screen still passes; that trade-off is deliberate because content
sniffing produced too many false negatives on redirect endpoints.

The probe issues ``GET`` with ``Range: bytes=0-0`` and closes the connection as
soon as the response headers arrive, so a multi-gigabyte subscription file is
never downloaded. ``HEAD`` is not used at all: many small hosts answer 405/403
to HEAD while serving GET normally.

A single failure is only ``suspected``. A source becomes ``unavailable`` after
``--threshold`` consecutive failures and returns to ``available`` automatically
as soon as a probe succeeds again.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.shared import ROOT, LIBRARY_CATEGORIES, write_json_atomic

LIBRARY_DIR = ROOT / "config/library"
TIMEOUT_SECONDS = 15.0
MAX_REDIRECTS = 5
DEFAULT_CONCURRENCY = 8
UNAVAILABLE_AFTER = 2

# The default urllib agent (``Python-urllib/3.x``) is rejected by many hosts.
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

CATEGORY_IDS = [key for key, _ in LIBRARY_CATEGORIES]
CATEGORY_LABELS = dict(LIBRARY_CATEGORIES)

STATUSES = ("unchecked", "available", "suspected", "unavailable", "disabled")


class LimitedRedirects(urllib.request.HTTPRedirectHandler):
    """Follow redirects, but stop instead of looping forever."""

    max_redirections = MAX_REDIRECTS


class ProbeResult:
    """Outcome of one availability probe."""

    __slots__ = ("ok", "status", "error", "latency_ms")

    def __init__(self, ok: bool, status: int | None, error: str, latency_ms: int) -> None:
        self.ok = ok
        self.status = status
        self.error = error
        self.latency_ms = latency_ms


def normalize_url(value: object) -> str:
    """Canonical form used as the entry key: scheme/host lowercased, no fragment."""

    raw = str(value or "").strip()
    try:
        parts = urllib.parse.urlsplit(raw)
    except ValueError:
        return raw
    if not parts.scheme or not parts.netloc:
        return raw
    netloc = parts.netloc.lower()
    return urllib.parse.urlunsplit((parts.scheme.lower(), netloc, parts.path, parts.query, ""))


def category_path(category: str) -> Path:
    return LIBRARY_DIR / f"{category}.json"


def display_path(path: Path) -> str:
    """Repo-relative path when possible; absolute otherwise (e.g. under test)."""

    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def load_category(category: str) -> dict:
    path = category_path(category)
    if not path.exists():
        print(f"  分类 {CATEGORY_LABELS.get(category, category)} 尚无文件，按空清单处理")
        return {"name": CATEGORY_LABELS.get(category, category), "sources": []}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data.get("sources"), list):
        raise ValueError(f"{path} 缺少 sources 数组")
    data.setdefault("name", CATEGORY_LABELS.get(category, category))
    return data


def describe_error(error: BaseException) -> str:
    if isinstance(error, (TimeoutError,)):
        return "超时"
    reason = getattr(error, "reason", None)
    if isinstance(reason, TimeoutError):
        return "超时"
    text = str(reason if reason is not None else error).strip() or error.__class__.__name__
    if "timed out" in text.lower():
        return "超时"
    return text


def probe(url: str, *, timeout: float = TIMEOUT_SECONDS, user_agent: str = USER_AGENT) -> ProbeResult:
    """Fetch response headers only and report whether the endpoint answers."""

    request = urllib.request.Request(
        url,
        method="GET",
        headers={
            "User-Agent": user_agent,
            "Range": "bytes=0-0",
            "Accept": "*/*",
            "Accept-Encoding": "identity",
        },
    )
    opener = urllib.request.build_opener(LimitedRedirects)
    started = time.monotonic()

    def elapsed() -> int:
        return int((time.monotonic() - started) * 1000)

    try:
        # urlopen resolves once the response headers are in; leaving the block
        # closes the connection without reading a body.
        with opener.open(request, timeout=timeout) as response:
            status = int(getattr(response, "status", 0) or response.getcode() or 0)
        return ProbeResult(200 <= status < 400, status, "", elapsed())
    except urllib.error.HTTPError as error:
        status = int(error.code)
        error.close()
        return ProbeResult(200 <= status < 400, status, f"HTTP {status}", elapsed())
    except BaseException as error:  # noqa: BLE001 - any transport failure is a failure
        return ProbeResult(False, None, describe_error(error), elapsed())


def apply_probe(
    entry: dict,
    result: ProbeResult,
    checked_at: str,
    *,
    threshold: int = UNAVAILABLE_AFTER,
) -> dict:
    """Fold one probe result into the stored entry. Mutates and returns it."""

    entry["lastCheckedAt"] = checked_at
    entry["httpStatus"] = result.status
    entry["latencyMs"] = result.latency_ms
    if result.ok:
        entry["status"] = "available"
        entry["failStreak"] = 0
        entry["lastOkAt"] = checked_at
        entry["error"] = ""
        return entry

    streak = int(entry.get("failStreak") or 0) + 1
    entry["failStreak"] = streak
    entry["status"] = "unavailable" if streak >= max(1, threshold) else "suspected"
    entry["error"] = result.error
    return entry


def is_disabled(entry: dict) -> bool:
    return entry.get("enabled") is False


def effective_status(entry: dict) -> str:
    if is_disabled(entry):
        return "disabled"
    status = str(entry.get("status") or "unchecked")
    return status if status in STATUSES else "unchecked"


def selected_entries(
    categories: list[str],
    *,
    only_url: str | None = None,
) -> tuple[list[tuple[str, dict]], dict[str, dict]]:
    """Return the entries to check plus the loaded category documents.

    The documents are handed back so the caller writes exactly the objects the
    probes mutated; reloading them from disk would drop every fresh result.
    """

    picked: list[tuple[str, dict]] = []
    loaded: dict[str, dict] = {}
    for category in categories:
        data = load_category(category)
        loaded[category] = data
        for entry in data["sources"]:
            if is_disabled(entry) or not str(entry.get("url") or "").strip():
                continue
            if only_url and normalize_url(entry["url"]) != normalize_url(only_url):
                continue
            picked.append((category, entry))
    return picked, loaded


def check_all(
    categories: list[str],
    *,
    concurrency: int = DEFAULT_CONCURRENCY,
    dry_run: bool = False,
    only_url: str | None = None,
    threshold: int = UNAVAILABLE_AFTER,
    timeout: float = TIMEOUT_SECONDS,
) -> dict:
    picked, loaded = selected_entries(categories, only_url=only_url)
    checked_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    print(f"检查 {len(picked)} 条源（并发 {max(1, concurrency)}，超时 {timeout:g}s）")

    counter = {"available": 0, "suspected": 0, "unavailable": 0, "disabled": 0, "unchecked": 0}
    lock = threading.Lock()
    rows: list[str] = []

    def work(item: tuple[str, dict]) -> None:
        category, entry = item
        result = probe(entry["url"], timeout=timeout)
        apply_probe(entry, result, checked_at, threshold=threshold)
        status = effective_status(entry)
        detail = f"HTTP {result.status}" if result.status else (result.error or "无响应")
        with lock:
            counter[status] = counter.get(status, 0) + 1
            rows.append(f"  {'✔' if result.ok else '✘'} [{CATEGORY_LABELS.get(category, category)}] {entry['title']} — {status} ({detail})")

    if picked:
        with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
            list(pool.map(work, picked))

    for line in rows:
        print(line)

    changed: list[str] = []
    if not dry_run:
        # Rewrite every touched category once, preserving file order.
        for category in dict.fromkeys(category for category, _ in picked):
            write_json_atomic(category_path(category), loaded[category])
            changed.append(display_path(category_path(category)))

    total = len(picked)
    summary = (
        f"检查完成：{total} 条 · 可用 {counter['available']} · 疑似 {counter['suspected']} "
        f"· 不可用 {counter['unavailable']} · 禁用 {counter['disabled']}"
    )
    print(summary)
    return {
        "checked": total,
        "counts": counter,
        "summary": summary,
        "written": changed,
        "dry_run": dry_run,
    }


def git_commit_and_push(paths: list[str]) -> bool:
    """Commit changed status files and push them. Returns whether a commit happened."""

    if not paths:
        print("没有需要提交的状态变化")
        return False
    subprocess.run(["git", "add", "--", *paths], cwd=ROOT, check=True)
    staged = subprocess.run(
        ["git", "diff", "--cached", "--quiet", "--", *paths],
        cwd=ROOT,
    )
    if staged.returncode == 0:
        print("状态文件没有实际变化，跳过提交")
        return False
    subprocess.run(
        ["git", "commit", "-m", "chore: update subscription source status"],
        cwd=ROOT,
        check=True,
    )
    subprocess.run(["git", "push"], cwd=ROOT, check=True)
    print("已提交并推送状态更新")
    return True


def resolve_categories(value: str | None) -> list[str]:
    if not value:
        return list(CATEGORY_IDS)
    requested = [item.strip() for item in value.split(",") if item.strip()]
    unknown = [item for item in requested if item not in CATEGORY_IDS]
    if unknown:
        raise SystemExit(f"未知分类：{', '.join(unknown)}（可选：{', '.join(CATEGORY_IDS)}）")
    return requested


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="检查订阅源可用性（只看 HTTP 状态码，不下载内容）",
    )
    parser.add_argument("--category", help="只检查指定分类，逗号分隔，例如 live,video")
    parser.add_argument("--url", help="只检查一条源（按规范化地址匹配）")
    parser.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY, help="并发数，默认 8")
    parser.add_argument("--timeout", type=float, default=TIMEOUT_SECONDS, help="单条超时秒数，默认 15")
    parser.add_argument("--threshold", type=int, default=UNAVAILABLE_AFTER, help="连续失败几次后判定不可用，默认 2")
    parser.add_argument("--dry-run", action="store_true", help="只打印结果，不写文件、不提交")
    parser.add_argument("--no-commit", action="store_true", help="写入状态文件但不提交")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    categories = resolve_categories(args.category)
    report = check_all(
        categories,
        concurrency=args.concurrency,
        dry_run=args.dry_run,
        only_url=args.url,
        threshold=args.threshold,
        timeout=args.timeout,
    )
    if not args.dry_run and not args.no_commit:
        git_commit_and_push(report["written"])
    return 1 if report["counts"]["unavailable"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
