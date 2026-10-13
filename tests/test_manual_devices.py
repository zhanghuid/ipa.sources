import unittest
from unittest import mock

from scripts.generate_android import hydrate_custom_catalog as hydrate_android
from scripts.generate_ios import hydrate_custom_catalog as hydrate_ios


class AndroidManualDeviceTests(unittest.TestCase):
    def test_manual_tags_are_used_without_downloading(self) -> None:
        catalog = {"apps": [{
            "name": "Demo",
            "packageName": "com.example.demo",
            "iconURL": "https://example.com/icon.png",
            "_iconExtractionChecked": "apk-icons-v1",
            "versions": [{
                "version": "1.0",
                "buildVersion": "1",
                "downloadURL": "https://example.com/tv.apk",
                "devices": ["tv"],
            }],
        }]}
        with mock.patch("scripts.generate_android.apk_metadata_for") as download:
            changed = hydrate_android(catalog)
        download.assert_not_called()
        self.assertTrue(changed)
        self.assertEqual(catalog["apps"][0]["devices"], ["tv"])

    def test_tags_merge_with_existing_detected_devices(self) -> None:
        catalog = {"apps": [{
            "name": "Demo",
            "packageName": "com.example.demo",
            "devices": ["phone", "pad"],
            "iconURL": "https://example.com/icon.png",
            "_iconExtractionChecked": "apk-icons-v1",
            "versions": [{
                "version": "1.0",
                "buildVersion": "1",
                "downloadURL": "https://example.com/tv.apk",
                "devices": ["tv"],
            }],
        }]}
        with mock.patch("scripts.generate_android.apk_metadata_for") as download:
            hydrate_android(catalog)
        download.assert_not_called()
        self.assertEqual(catalog["apps"][0]["devices"], ["tv", "phone", "pad"])

    def test_unknown_tags_fall_back_to_the_manifest(self) -> None:
        # An unrecognised tag is not a manual override, so the archive is parsed
        # and the manifest decides the devices.
        catalog = {"apps": [{
            "name": "Demo",
            "packageName": "com.example.demo",
            "iconURL": "https://example.com/icon.png",
            "_iconExtractionChecked": "apk-icons-v1",
            "versions": [{
                "version": "1.0",
                "buildVersion": "1",
                "downloadURL": "https://example.com/a.apk",
                "devices": ["fridge"],
            }],
        }]}
        metadata = {
            "packageName": "com.example.demo",
            "versionName": "1.0",
            "versionCode": 1,
            "size": 10,
            "devices": ["phone"],
        }
        with mock.patch("scripts.generate_android.apk_metadata_for", return_value=metadata) as download:
            hydrate_android(catalog)
        download.assert_called_once()
        self.assertEqual(catalog["apps"][0]["devices"], ["phone"])


class IosManualDeviceTests(unittest.TestCase):
    def test_manual_tags_skip_the_archive_download(self) -> None:
        catalog = {"apps": [{
            "name": "Demo",
            "bundleIdentifier": "com.example.demo",
            "iconURL": "https://example.com/icon.png",
            "_iconExtractionChecked": "cgbi-v2",
            "versions": [{
                "version": "1.0",
                "buildVersion": "1",
                "downloadURL": "https://example.com/tv.ipa",
                "devices": ["tv"],
            }],
        }]}
        with mock.patch("scripts.generate_ios.ipa_metadata") as download:
            hydrate_ios(catalog)
        download.assert_not_called()
        self.assertEqual(catalog["apps"][0]["devices"], ["tv"])


if __name__ == "__main__":
    unittest.main()
