import unittest

from scripts.generate_android import merge_catalogs as merge_android
from scripts.generate_ios import merge_catalogs as merge_ios
from scripts.shared import add_version_record, normalize_devices


class NormalizeDevicesTests(unittest.TestCase):
    def test_lowercases_and_drops_unknown_classes(self) -> None:
        self.assertEqual(normalize_devices(["TV", "Phone", "fridge"]), ["phone", "tv"])

    def test_returns_canonical_order_regardless_of_input_order(self) -> None:
        self.assertEqual(normalize_devices(["vision", "phone", "tv"]), ["phone", "tv", "vision"])

    def test_ignores_blank_and_non_list_values(self) -> None:
        self.assertEqual(normalize_devices(["", "  ", "tv"]), ["tv"])
        self.assertEqual(normalize_devices(None), [])
        self.assertEqual(normalize_devices("tv"), [])


class VersionIdentityTests(unittest.TestCase):
    """Version numbers repeat across device builds, so the URL must separate them."""

    def test_same_version_different_address_keeps_both(self) -> None:
        store = {}
        add_version_record(store, {"version": "2.6.1", "buildVersion": "1",
                                   "downloadURL": "https://example.com/mobile.apk", "devices": ["phone"]})
        add_version_record(store, {"version": "2.6.1", "buildVersion": "1",
                                   "downloadURL": "https://example.com/tv.apk", "devices": ["tv"]})
        self.assertEqual(len(store), 2)

    def test_same_address_from_two_catalogs_collapses_and_unions_devices(self) -> None:
        store = {}
        url = "https://example.com/app.apk"
        add_version_record(store, {"version": "1.0", "buildVersion": "5", "date": "2026-10-01",
                                   "downloadURL": url, "devices": ["phone"]})
        add_version_record(store, {"version": "1.0", "buildVersion": "5", "date": "2026-10-13",
                                   "downloadURL": url, "devices": ["tv"], "size": 100})
        self.assertEqual(len(store), 1)
        stored = next(iter(store.values()))[1]
        self.assertEqual(stored["devices"], ["phone", "tv"])
        self.assertEqual(stored["size"], 100)

    def test_pending_addresses_do_not_collapse(self) -> None:
        store = {}
        add_version_record(store, {"version": "pending", "buildVersion": "pending",
                                   "downloadURL": "https://example.com/a.apk"})
        add_version_record(store, {"version": "pending", "buildVersion": "pending",
                                   "downloadURL": "https://example.com/b.apk"})
        self.assertEqual(len(store), 2)


class ManualDeviceMergeTests(unittest.TestCase):
    """A per-address tag must reach the app-level devices list."""

    def test_android_tags_merge_across_addresses(self) -> None:
        catalog = {"apps": [{
            "name": "小猫影视",
            "packageName": "io.github.d1y.catmovie",
            "devices": [],
            "versions": [
                {"version": "2.6.1", "buildVersion": "1", "date": "2026-10-13",
                 "downloadURL": "https://example.com/mobile.apk", "devices": ["phone", "pad"]},
                {"version": "2.6.1", "buildVersion": "2", "date": "2026-10-13",
                 "downloadURL": "https://example.com/tv.apk", "devices": ["tv"]},
            ],
        }]}
        merged = merge_android([("custom", catalog)], "", ["phone", "pad"])
        self.assertEqual(merged[0]["devices"], ["phone", "pad", "tv"])
        self.assertEqual(len(merged[0]["versions"]), 2)

    def test_ios_tags_merge_across_addresses(self) -> None:
        catalog = {"apps": [{
            "name": "Demo",
            "bundleIdentifier": "com.example.demo",
            "versions": [
                {"version": "1.0", "buildVersion": "1", "date": "2026-10-13",
                 "downloadURL": "https://example.com/tv.ipa", "devices": ["tv"]},
                {"version": "1.0", "buildVersion": "1", "date": "2026-10-13",
                 "downloadURL": "https://example.com/phone.ipa", "devices": ["phone", "pad"]},
            ],
        }]}
        merged = merge_ios([("custom", catalog)], "", ["phone", "pad"])
        self.assertEqual(merged[0]["devices"], ["phone", "pad", "tv"])

    def test_app_level_tags_are_kept_alongside_address_tags(self) -> None:
        catalog = {"apps": [{
            "name": "Demo",
            "bundleIdentifier": "com.example.demo",
            "devices": ["watch"],
            "versions": [
                {"version": "1.0", "buildVersion": "1", "date": "2026-10-13",
                 "downloadURL": "https://example.com/tv.ipa", "devices": ["tv"]},
            ],
        }]}
        merged = merge_ios([("custom", catalog)], "", ["phone", "pad"])
        self.assertEqual(merged[0]["devices"], ["tv", "watch"])

    def test_defaults_apply_only_without_any_device_information(self) -> None:
        catalog = {"apps": [{
            "name": "Bare",
            "bundleIdentifier": "com.example.bare",
            "versions": [
                {"version": "1.0", "buildVersion": "1", "date": "2026-10-13",
                 "downloadURL": "https://example.com/bare.ipa"},
            ],
        }]}
        merged = merge_ios([("custom", catalog)], "", ["phone", "pad"])
        self.assertEqual(merged[0]["devices"], ["phone", "pad"])

    def test_pending_addresses_survive_as_separate_versions(self) -> None:
        # Two addresses awaiting their first parse share "pending" as version and
        # build, so keying on those alone would collapse them into one.
        catalog = {"apps": [{
            "name": "小猫影视",
            "packageName": "io.github.d1y.catmovie",
            "versions": [
                {"version": "pending", "buildVersion": "pending", "date": "2026-10-13",
                 "downloadURL": "https://example.com/mobile.apk", "devices": ["phone"]},
                {"version": "pending", "buildVersion": "pending", "date": "2026-10-13",
                 "downloadURL": "https://example.com/tv.apk", "devices": ["tv"]},
            ],
        }]}
        merged = merge_android([("custom", catalog)], "", ["phone", "pad"])
        self.assertEqual(len(merged[0]["versions"]), 2)
        self.assertEqual(merged[0]["devices"], ["phone", "tv"])

    def test_published_versions_still_deduplicate(self) -> None:
        # The same release reported by two catalogs must remain one version.
        a = {"apps": [{"name": "X", "packageName": "com.x", "versions": [
            {"version": "1.0", "buildVersion": "5", "date": "2026-10-01",
             "downloadURL": "https://example.com/a.apk"}]}]}
        b = {"apps": [{"name": "X", "packageName": "com.x", "versions": [
            {"version": "1.0", "buildVersion": "5", "date": "2026-10-13",
             "downloadURL": "https://example.com/a.apk", "devices": ["tv"]}]}]}
        merged = merge_android([("first", a), ("second", b)], "", ["phone", "pad"])
        self.assertEqual(len(merged[0]["versions"]), 1)
        self.assertEqual(merged[0]["devices"], ["tv"])


if __name__ == "__main__":
    unittest.main()
