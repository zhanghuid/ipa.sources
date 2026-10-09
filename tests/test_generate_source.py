import io
import struct
import unittest
import zipfile
import zlib

from scripts.generate_source import extract_icon_png, normalize_cgbi_png, png_chunks, png_dimensions


def png_chunk(kind: bytes, data: bytes) -> bytes:
    body = kind + data
    return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)


def make_png(width: int, height: int) -> bytes:
    header = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + png_chunk(b"IHDR", header)
        + png_chunk(b"IDAT", b"not-decoded-by-dimension-reader")
        + png_chunk(b"IEND", b"")
    )


def make_cgbi_png() -> bytes:
    # One premultiplied BGRA pixel representing RGBA(100, 50, 20, 128).
    raw = b"\x00" + bytes((10, 25, 50, 128))
    compressor = zlib.compressobj(wbits=-15)
    compressed = compressor.compress(raw) + compressor.flush()
    header = struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + png_chunk(b"CgBI", b"\x50\x00\x20\x06")
        + png_chunk(b"IHDR", header)
        + png_chunk(b"IDAT", compressed)
        + png_chunk(b"IEND", b"")
    )


class ExtractIconPngTests(unittest.TestCase):
    def test_uses_largest_png_matching_primary_icon_files(self) -> None:
        app_root = "Payload/Runner.app"
        small_icon = make_png(120, 120)
        large_icon = make_png(180, 180)
        unrelated = make_png(1024, 1024)
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr(f"{app_root}/AppIcon60x60@2x.png", small_icon)
            archive.writestr(f"{app_root}/AppIcon60x60@3x.png", large_icon)
            archive.writestr(f"{app_root}/Splash.png", unrelated)
        buffer.seek(0)

        with zipfile.ZipFile(buffer) as archive:
            names = archive.namelist()
            result = extract_icon_png(
                archive,
                app_root,
                {"CFBundleIcons": {"CFBundlePrimaryIcon": {"CFBundleIconFiles": ["AppIcon60x60"]}}},
                names,
            )

        self.assertEqual(result, large_icon)

    def test_falls_back_to_appicon_named_png(self) -> None:
        app_root = "Payload/App.app"
        expected = make_png(1024, 1024)
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr(f"{app_root}/AppIcon.png", expected)
        buffer.seek(0)

        with zipfile.ZipFile(buffer) as archive:
            result = extract_icon_png(archive, app_root, {}, archive.namelist())

        self.assertEqual(result, expected)

    def test_converts_cgbi_png_to_standard_rgba(self) -> None:
        converted = normalize_cgbi_png(make_cgbi_png())
        chunks = dict(png_chunks(converted))

        self.assertEqual(png_dimensions(converted), (1, 1))
        self.assertNotIn(b"CgBI", chunks)
        self.assertEqual(zlib.decompress(chunks[b"IDAT"]), b"\x00\x64\x32\x14\x80")


if __name__ == "__main__":
    unittest.main()
