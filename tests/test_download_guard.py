import io
import unittest
import urllib.request
from unittest import mock

from scripts.shared import download_to_tempfile, looks_like_html


class FakeResponse(io.BytesIO):
    def __init__(self, payload: bytes, content_type: str = "") -> None:
        super().__init__(payload)
        self.headers = {"Content-Type": content_type, "Content-Length": str(len(payload))}
        self._url = "https://example.com/app.apk"

    def geturl(self) -> str:
        return self._url

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> None:
        return None


class LooksLikeHtmlTests(unittest.TestCase):
    def test_detects_doctype_and_html_tag(self) -> None:
        self.assertTrue(looks_like_html(b"<!DOCTYPE html><html lang='en'>"))
        self.assertTrue(looks_like_html(b"\n  <html><head>"))
        self.assertTrue(looks_like_html(b"  <!doctype html>"))

    def test_does_not_flag_binary_archives(self) -> None:
        self.assertFalse(looks_like_html(b"PK\x03\x04\x14\x00\x00\x00"))
        self.assertFalse(looks_like_html(b"\x00\x01\x02\x03"))


class DownloadGuardTests(unittest.TestCase):
    """A directory page must fail close to the cause, not as a zip parse error."""

    def _download(self, response):
        with mock.patch.object(urllib.request, "urlopen", return_value=response):
            return download_to_tempfile("https://example.com/app.apk", "Demo", 1024 * 1024)

    def test_rejects_html_content_type(self) -> None:
        with self.assertRaises(ValueError) as raised:
            self._download(FakeResponse(b"<html></html>", "text/html; charset=utf-8"))
        self.assertIn("HTML", str(raised.exception))

    def test_rejects_html_body_without_content_type(self) -> None:
        with self.assertRaises(ValueError) as raised:
            self._download(FakeResponse(b"<!DOCTYPE html><html><body>Not found</body></html>"))
        self.assertIn("HTML", str(raised.exception))

    def test_accepts_binary_payload(self) -> None:
        temporary, size = self._download(FakeResponse(b"PK\x03\x04" + b"\x00" * 64))
        try:
            self.assertEqual(size, 68)
        finally:
            temporary.close()


if __name__ == "__main__":
    unittest.main()
