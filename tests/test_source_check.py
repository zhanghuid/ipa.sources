"""Availability checks for the subscription sources, exercised against a local
server so the suite never touches the network."""

import json
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from scripts import check_sources


class Handler(BaseHTTPRequestHandler):
    """Serves a handful of fixed responses plus one deliberately slow route."""

    counter: dict[str, int] = {}

    def log_message(self, *args) -> None:  # keep the test output readable
        pass

    def do_GET(self) -> None:
        path = self.path.split("?")[0]
        Handler.counter[path] = Handler.counter.get(path, 0) + 1
        if path == "/ok.m3u":
            body = b"#EXTM3U\n"
            self.send_response(200)
        elif path == "/moved.m3u":
            self.send_response(302)
            self.send_header("Location", "/ok.m3u")
            body = b""
        elif path == "/missing.m3u":
            body = b"gone"
            self.send_response(404)
        elif path == "/forbidden.m3u":
            body = b"nope"
            self.send_response(403)
        elif path == "/broken.m3u":
            body = b"oops"
            self.send_response(500)
        elif path == "/slow.m3u":
            time.sleep(0.4)
            body = b"#EXTM3U\n"
            self.send_response(200)
        elif path == "/flaky.m3u":
            # Fails the first request, then recovers.
            if Handler.counter[path] == 1:
                body = b"down"
                self.send_response(503)
            else:
                body = b"#EXTM3U\n"
                self.send_response(200)
        else:
            body = b"?"
            self.send_response(404)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)


class LocalServer:
    def __enter__(self):
        Handler.counter = {}
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        return self

    def __exit__(self, *_) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)


class ProbeTests(unittest.TestCase):
    def test_available_redirect_and_failures(self) -> None:
        with LocalServer() as server:
            self.assertEqual(check_sources.probe(f"{server.base}/ok.m3u").status, 200)
            moved = check_sources.probe(f"{server.base}/moved.m3u")
            self.assertTrue(moved.ok)
            self.assertEqual(moved.status, 200)
            for path in ("missing.m3u", "forbidden.m3u", "broken.m3u"):
                result = check_sources.probe(f"{server.base}/{path}")
                self.assertFalse(result.ok, path)
                self.assertGreaterEqual(result.status, 400)

    def test_timeout_is_reported_as_failure(self) -> None:
        with LocalServer() as server:
            result = check_sources.probe(f"{server.base}/slow.m3u", timeout=0.1)
            self.assertFalse(result.ok)
            self.assertIsNone(result.status)
            self.assertEqual(result.error, "超时")

    def test_connection_error_is_a_failure(self) -> None:
        # Nothing is listening on this port.
        result = check_sources.probe("http://127.0.0.1:9/never.m3u", timeout=1)
        self.assertFalse(result.ok)
        self.assertIsNone(result.status)


class ApplyProbeTests(unittest.TestCase):
    def test_single_failure_is_suspected_then_unavailable_then_recovers(self) -> None:
        entry = {"title": "示例", "url": "https://example.com/a.m3u", "status": "unchecked"}
        failure = check_sources.ProbeResult(False, 503, "HTTP 503", 12)
        success = check_sources.ProbeResult(True, 200, "", 8)

        check_sources.apply_probe(entry, failure, "2026-10-13T00:00:00Z")
        self.assertEqual(entry["status"], "suspected")
        self.assertEqual(entry["failStreak"], 1)

        check_sources.apply_probe(entry, failure, "2026-10-14T00:00:00Z")
        self.assertEqual(entry["status"], "unavailable")
        self.assertEqual(entry["failStreak"], 2)
        self.assertEqual(entry["httpStatus"], 503)
        self.assertEqual(entry["error"], "HTTP 503")

        check_sources.apply_probe(entry, success, "2026-10-15T00:00:00Z")
        self.assertEqual(entry["status"], "available")
        self.assertEqual(entry["failStreak"], 0)
        self.assertEqual(entry["lastOkAt"], "2026-10-15T00:00:00Z")
        self.assertEqual(entry["error"], "")

    def test_disabled_entries_are_not_checked(self) -> None:
        entry = {"title": "停用", "url": "https://example.com/b.m3u", "enabled": False, "status": "available"}
        self.assertTrue(check_sources.is_disabled(entry))
        self.assertEqual(check_sources.effective_status(entry), "disabled")

    def test_custom_threshold(self) -> None:
        entry = {"title": "严格", "url": "https://example.com/c.m3u"}
        failure = check_sources.ProbeResult(False, 500, "HTTP 500", 5)
        check_sources.apply_probe(entry, failure, "2026-10-13T00:00:00Z", threshold=1)
        self.assertEqual(entry["status"], "unavailable")


class NormalizeTests(unittest.TestCase):
    def test_normalizes_case_and_strips_fragment(self) -> None:
        self.assertEqual(
            check_sources.normalize_url("HTTPS://Example.COM/Path?A=1#frag"),
            "https://example.com/Path?A=1",
        )


class CheckAllTests(unittest.TestCase):
    """End-to-end run against the local server, writing into a temp library dir."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.library_dir = Path(self.temporary.name) / "config/library"
        self.library_dir.mkdir(parents=True)
        self.patcher = mock.patch.object(check_sources, "LIBRARY_DIR", self.library_dir)
        self.patcher.start()

    def tearDown(self) -> None:
        self.patcher.stop()
        self.temporary.cleanup()

    def write_category(self, category: str, sources: list[dict]) -> None:
        (self.library_dir / f"{category}.json").write_text(
            json.dumps({"name": category, "sources": sources}, ensure_ascii=False),
            encoding="utf-8",
        )

    def test_writes_status_and_returns_unavailable_count(self) -> None:
        with LocalServer() as server:
            self.write_category("live", [
                {"title": "正常", "url": f"{server.base}/ok.m3u"},
                {"title": "失效", "url": f"{server.base}/missing.m3u"},
                {"title": "停用", "url": f"{server.base}/ok.m3u?off", "enabled": False},
            ])
            report = check_sources.check_all(["live"], concurrency=2)

        self.assertEqual(report["checked"], 2)
        self.assertEqual(report["counts"]["available"], 1)
        self.assertEqual(report["counts"]["suspected"], 1)
        stored = json.loads((self.library_dir / "live.json").read_text(encoding="utf-8"))
        by_title = {entry["title"]: entry for entry in stored["sources"]}
        self.assertEqual(by_title["正常"]["status"], "available")
        self.assertEqual(by_title["失效"]["status"], "suspected")
        self.assertEqual(by_title["失效"]["httpStatus"], 404)
        # Disabled is an independent marker: it is never probed, so it stores no
        # status. The generator projects it as "disabled" for clients.
        self.assertIs(by_title["停用"]["enabled"], False)
        self.assertNotIn("status", by_title["停用"])
        self.assertNotIn("lastCheckedAt", by_title["停用"])

    def test_dry_run_leaves_the_file_untouched(self) -> None:
        with LocalServer() as server:
            original = [{"title": "正常", "url": f"{server.base}/ok.m3u"}]
            self.write_category("live", original)
            before = (self.library_dir / "live.json").read_text(encoding="utf-8")
            report = check_sources.check_all(["live"], dry_run=True)

        self.assertTrue(report["dry_run"])
        self.assertEqual(report["written"], [])
        after = (self.library_dir / "live.json").read_text(encoding="utf-8")
        self.assertEqual(before, after)

    def test_second_run_marks_a_persistently_broken_source_unavailable(self) -> None:
        with LocalServer() as server:
            self.write_category("video", [{"title": "坏源", "url": f"{server.base}/broken.m3u"}])
            check_sources.check_all(["video"], threshold=1)
            report = check_sources.check_all(["video"], threshold=1)

        self.assertEqual(report["counts"]["unavailable"], 1)
        stored = json.loads((self.library_dir / "video.json").read_text(encoding="utf-8"))
        self.assertEqual(stored["sources"][0]["status"], "unavailable")

    def test_recovery_returns_the_source_to_available(self) -> None:
        with LocalServer() as server:
            self.write_category("audio", [{"title": "抖动", "url": f"{server.base}/flaky.m3u"}])
            first = check_sources.check_all(["audio"])
            second = check_sources.check_all(["audio"])

        self.assertEqual(first["counts"]["suspected"], 1)
        self.assertEqual(second["counts"]["available"], 1)
        stored = json.loads((self.library_dir / "audio.json").read_text(encoding="utf-8"))
        self.assertEqual(stored["sources"][0]["status"], "available")
        self.assertEqual(stored["sources"][0]["failStreak"], 0)


if __name__ == "__main__":
    unittest.main()
