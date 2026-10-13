import struct
import unittest

from scripts.apk_metadata import ApkParseError, parse_axml


def string_pool(strings: list[str]) -> bytes:
    """Build a UTF-8 string pool chunk."""
    encoded = [value.encode("utf-8") for value in strings]
    offsets: list[int] = []
    payload = bytearray()
    for value in encoded:
        offsets.append(len(payload))
        # UTF-8 length then byte length, both single-byte for short ASCII names.
        payload.append(len(value))
        payload.append(len(value))
        payload.extend(value)
        payload.append(0)
    header_size = 28
    strings_start = header_size + len(offsets) * 4
    body_size = strings_start + len(payload)
    chunk = bytearray()
    chunk += struct.pack("<HHI", 0x0001, header_size, body_size)
    chunk += struct.pack("<IIIII", len(strings), 0, 0x100, strings_start, 0)
    for offset in offsets:
        chunk += struct.pack("<I", offset)
    chunk += payload
    return bytes(chunk)


def start_element(name_index: int, attributes: list[tuple[int, int, int, int]]) -> bytes:
    """Build a start-element chunk with typed attributes.

    Layout: ResChunk_header plus line/comment (16 bytes), then
    ResXMLTree_attrExt (20 bytes:
    namespace, name, attributeStart, attributeSize, attributeCount, idIndex,
    classIndex, styleIndex), then the attribute array at offset 36.
    """
    attrs = bytearray()
    for ns, name, raw, value in attributes:
        # ResXMLTree_attribute is exactly 20 bytes.
        attrs += struct.pack("<III", ns, name, raw)
        attrs += struct.pack("<HBBI", 8, 0, 0x10, value)  # size, res0, TYPE_INT_DEC, data

    # attributeStart is relative to attrExt, and attrExt is 20 bytes long.
    attr_ext = struct.pack("<II", 0xFFFFFFFF, name_index)
    attr_ext += struct.pack("<HHHHHH", 20, 20, len(attributes), 0, 0, 0)
    size = 16 + len(attr_ext) + len(attrs)
    header = struct.pack("<HHIII", 0x0102, 16, size, 1, 0xFFFFFFFF)
    return header + attr_ext + bytes(attrs)


def build_manifest() -> bytes:
    strings = [
        "manifest", "package", "versionCode", "uses-sdk", "minSdkVersion",
        "application", "icon", "uses-feature", "name", "required",
        "android.software.leanback", "android.hardware.touchscreen",
    ]
    index = {value: position for position, value in enumerate(strings)}
    chunks = [
        string_pool(strings),
        start_element(index["manifest"], [
            (0xFFFFFFFF, index["package"], 0xFFFFFFFF, 0),
            (0xFFFFFFFF, index["versionCode"], 0xFFFFFFFF, 2446),
        ]),
        start_element(index["uses-sdk"], [(0xFFFFFFFF, index["minSdkVersion"], 0xFFFFFFFF, 17)]),
        start_element(index["uses-feature"], [
            (0xFFFFFFFF, index["name"], 0xFFFFFFFF, 0),
            (0xFFFFFFFF, index["required"], 0xFFFFFFFF, 0),
        ]),
    ]
    body = b"".join(chunks)
    return struct.pack("<HHI", 0x0003, 8, 8 + len(body)) + body


class ParseAxmlTests(unittest.TestCase):
    def test_reads_element_names_and_typed_attributes(self) -> None:
        elements = parse_axml(build_manifest())
        names = [element["name"] for element in elements]
        self.assertEqual(names, ["manifest", "uses-sdk", "uses-feature"])
        manifest = elements[0]
        self.assertIn("package", manifest["attributes"])
        self.assertEqual(manifest["attributes"]["versionCode"]["value"].data, 2446)
        self.assertEqual(elements[1]["attributes"]["minSdkVersion"]["value"].data, 17)

    def test_rejects_non_xml_payload(self) -> None:
        with self.assertRaises(ApkParseError):
            parse_axml(b"<manifest />")

    def test_rejects_truncated_chunk(self) -> None:
        payload = build_manifest()
        with self.assertRaises(ApkParseError):
            parse_axml(payload[:12] + b"\x00" * 2)
