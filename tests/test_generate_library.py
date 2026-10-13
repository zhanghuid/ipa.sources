"""Publishing rules for the subscription source feeds."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts import generate_library


class GenerateLibraryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.library_dir = self.root / "config/library"
        self.output_dir = self.root / "sources"
        self.library_dir.mkdir(parents=True)
        self.patchers = [
            mock.patch.object(generate_library, "LIBRARY_DIR", self.library_dir),
            mock.patch.object(generate_library, "OUTPUT_DIR", self.output_dir),
        ]
        for patcher in self.patchers:
            patcher.start()

    def tearDown(self) -> None:
        for patcher in self.patchers:
            patcher.stop()
        self.temporary.cleanup()

    def write(self, category: str, sources: list[dict]) -> None:
        (self.library_dir / f"{category}.json").write_text(
            json.dumps({"name": category, "sources": sources}, ensure_ascii=False),
            encoding="utf-8",
        )

    def read(self, name: str) -> dict:
        return json.loads((self.output_dir / name).read_text(encoding="utf-8"))

    def test_missing_category_files_publish_empty_feeds(self) -> None:
        generate_library.build()
        index = self.read("index.json")
        self.assertEqual(len(index["categories"]), 6)
        self.assertTrue(all(item["total"] == 0 for item in index["categories"]))
        self.assertEqual(self.read("available.json")["sources"], [])

    def test_internal_bookkeeping_is_not_published(self) -> None:
        self.write("live", [{
            "title": "可用源",
            "url": "https://example.com/a.m3u",
            "status": "available",
            "lastCheckedAt": "2026-10-13T00:00:00Z",
            "httpStatus": 200,
            "failStreak": 0,
            "lastOkAt": "2026-10-13T00:00:00Z",
            "error": "",
            "latencyMs": 42,
        }])
        generate_library.build()
        entry = self.read("live.json")["sources"][0]
        self.assertEqual(entry["status"], "available")
        self.assertEqual(entry["httpStatus"], 200)
        self.assertEqual(entry["lastCheckedAt"], "2026-10-13T00:00:00Z")
        for internal in ("failStreak", "latencyMs", "lastOkAt", "error"):
            self.assertNotIn(internal, entry)

    def test_unavailable_sources_stay_in_the_full_feeds(self) -> None:
        self.write("video", [
            {"title": "好", "url": "https://example.com/a.json", "status": "available"},
            {"title": "坏", "url": "https://example.com/b.json", "status": "unavailable", "httpStatus": 500},
            {"title": "停", "url": "https://example.com/c.json", "enabled": False},
            {"title": "未知", "url": "https://example.com/d.json"},
        ])
        generate_library.build()
        published = self.read("video.json")["sources"]
        self.assertEqual([item["title"] for item in published], ["好", "坏", "停", "未知"])
        self.assertEqual([item["status"] for item in published],
                         ["available", "unavailable", "disabled", "unchecked"])
        # Only genuinely available sources reach the strict file.
        available = self.read("available.json")["sources"]
        self.assertEqual([item["title"] for item in available], ["好"])
        self.assertEqual(available[0]["category"], "video")
        self.assertEqual(available[0]["categoryName"], "视频源")

    def test_index_counts_match_the_category_files(self) -> None:
        self.write("live", [
            {"title": "A", "url": "https://example.com/a.m3u", "status": "available"},
            {"title": "B", "url": "https://example.com/b.m3u", "status": "unavailable"},
        ])
        generate_library.build()
        live = next(item for item in self.read("index.json")["categories"] if item["id"] == "live")
        self.assertEqual(live["total"], 2)
        self.assertEqual(live["available"], 1)
        self.assertEqual(live["file"], "live.json")


if __name__ == "__main__":
    unittest.main()
