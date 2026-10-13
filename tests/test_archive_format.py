import unittest

from scripts.shared import classify_archive, format_mismatch_message


class ClassifyArchiveTests(unittest.TestCase):
    def test_detects_ipa_layout(self) -> None:
        names = ["Payload/Demo.app/Info.plist", "Payload/Demo.app/Demo", "SwiftSupport/lib.swiftmodule"]
        self.assertEqual(classify_archive(names), "ipa")

    def test_detects_apk_layout(self) -> None:
        names = ["AndroidManifest.xml", "resources.arsc", "classes.dex", "res/mipmap/ic.png"]
        self.assertEqual(classify_archive(names), "apk")

    def test_reports_ambiguous_when_both_layouts_present(self) -> None:
        names = ["AndroidManifest.xml", "Payload/Demo.app/Info.plist"]
        self.assertEqual(classify_archive(names), "ambiguous")

    def test_reports_unknown_for_unrelated_zip(self) -> None:
        self.assertEqual(classify_archive(["readme.txt", "images/a.png"]), "unknown")


class FormatMismatchTests(unittest.TestCase):
    def test_flags_apk_submitted_to_ios(self) -> None:
        message = format_mismatch_message("Demo", "ipa", "apk")
        self.assertIsNotNone(message)
        self.assertIn("APK", message)
        self.assertIn("Android", message)

    def test_flags_ipa_submitted_to_android(self) -> None:
        message = format_mismatch_message("Demo", "apk", "ipa")
        self.assertIsNotNone(message)
        self.assertIn("IPA", message)
        self.assertIn("iOS", message)

    def test_accepts_matching_format(self) -> None:
        self.assertIsNone(format_mismatch_message("Demo", "ipa", "ipa"))
        self.assertIsNone(format_mismatch_message("Demo", "apk", "apk"))

    def test_does_not_guess_on_unknown_or_ambiguous(self) -> None:
        self.assertIsNone(format_mismatch_message("Demo", "ipa", "unknown"))
        self.assertIsNone(format_mismatch_message("Demo", "apk", "ambiguous"))


if __name__ == "__main__":
    unittest.main()
