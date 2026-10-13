"""Read package metadata and launcher icons from Android APK archives.

The module deliberately uses only the standard library. AndroidManifest.xml and
resources.arsc inside an APK are binary formats; the small readers below cover
the subset needed to extract package name, version, minimum SDK, declared
device features, and the launcher icon referenced by ``android:icon``.
"""

from __future__ import annotations

import re
import struct
import zipfile
from pathlib import Path

ANDROID_NS = "http://schemas.android.com/apk/res/android"

CHUNK_STRING_POOL = 0x0001
CHUNK_TABLE = 0x0002
CHUNK_XML = 0x0003
CHUNK_XML_START_ELEMENT = 0x0102
CHUNK_XML_END_ELEMENT = 0x0103
CHUNK_TABLE_PACKAGE = 0x0200
CHUNK_TABLE_TYPE = 0x0201
CHUNK_TABLE_TYPE_SPEC = 0x0202

FLAG_COMPLEX = 0x0001

TYPE_REFERENCE = 0x01
TYPE_STRING = 0x03
TYPE_INT_DEC = 0x10
TYPE_INT_HEX = 0x11
TYPE_INT_BOOLEAN = 0x12

DENSITY_SCALE = {0: 160, 120: 120, 160: 160, 213: 213, 240: 240, 320: 320, 480: 480, 640: 640}


class ApkParseError(ValueError):
    """Raised when an APK contains structures the reader cannot interpret."""


def _read_string_pool(data: bytes) -> list[str]:
    if struct.unpack_from("<H", data, 0)[0] != CHUNK_STRING_POOL:
        raise ApkParseError("expected a string pool chunk")
    string_count, _style_count, flags, strings_start, _styles_start = struct.unpack_from("<IIIII", data, 8)
    utf8 = bool(flags & 0x100)
    if string_count == 0:
        return []
    if 28 + string_count * 4 > len(data):
        raise ApkParseError("string pool offset table exceeds chunk size")
    offsets = struct.unpack_from(f"<{string_count}I", data, 28)
    strings: list[str] = []
    for offset in offsets:
        position = strings_start + offset
        if position >= len(data):
            raise ApkParseError("string pool entry points past the chunk")
        if utf8:
            length = data[position]
            position += 1
            if length & 0x80:
                length = ((length & 0x7F) << 8) | data[position]
                position += 1
            byte_length = data[position]
            position += 1
            if byte_length & 0x80:
                byte_length = ((byte_length & 0x7F) << 8) | data[position]
                position += 1
            strings.append(data[position:position + byte_length].decode("utf-8", "replace"))
        else:
            length = struct.unpack_from("<H", data, position)[0]
            position += 2
            if length & 0x8000:
                length = ((length & 0x7FFF) << 16) | struct.unpack_from("<H", data, position)[0]
                position += 2
            strings.append(data[position:position + length * 2].decode("utf-16-le", "replace"))
    return strings


def _string_at(pool: list[str] | None, reference: int) -> str | None:
    if pool is None or reference == 0xFFFFFFFF:
        return None
    index = reference & 0x00FFFFFF
    if index >= len(pool):
        return None
    return pool[index]


class ResValue:
    """A ``Res_value`` struct: a typed payload used by manifest and resource entries."""

    __slots__ = ("data_type", "data")

    def __init__(self, data_type: int, data: int) -> None:
        self.data_type = data_type
        self.data = data

    def __repr__(self) -> str:  # pragma: no cover - debugging helper
        return f"ResValue(type=0x{self.data_type:02x}, data=0x{self.data:08x})"


def _read_res_value(data: bytes, offset: int) -> ResValue:
    _size, _res0, data_type, value = struct.unpack_from("<HBBI", data, offset)
    return ResValue(data_type, value)


def parse_axml(data: bytes) -> list[dict]:
    """Parse binary Android XML into a flat element list with resolved attributes."""
    if len(data) < 8 or struct.unpack_from("<H", data, 0)[0] != CHUNK_XML:
        raise ApkParseError("not a binary Android XML document")
    header_size = struct.unpack_from("<H", data, 2)[0]
    total_size = struct.unpack_from("<I", data, 4)[0]
    if total_size > len(data):
        raise ApkParseError("binary XML declares a size larger than the file")
    strings: list[str] | None = None
    elements: list[dict] = []
    offset = header_size
    while offset + 8 <= total_size:
        chunk_type, _header, chunk_size = struct.unpack_from("<HHI", data, offset)
        if chunk_size < 8 or offset + chunk_size > total_size:
            raise ApkParseError("binary XML chunk has an invalid size")
        if chunk_type == CHUNK_STRING_POOL:
            strings = _read_string_pool(data[offset:offset + chunk_size])
        elif chunk_type in (CHUNK_XML_START_ELEMENT, CHUNK_XML_END_ELEMENT):
            _line, _comment, namespace, name = struct.unpack_from("<IIII", data, offset + 8)
            element = {
                "name": _string_at(strings, name),
                "namespace": _string_at(strings, namespace),
                "attributes": {},
            }
            if chunk_type == CHUNK_XML_START_ELEMENT:
                attr_start, attr_size, attr_count = struct.unpack_from("<HHH", data, offset + 24)
                for index in range(attr_count):
                    position = offset + 16 + attr_start + index * attr_size
                    if position + 20 > offset + chunk_size:
                        raise ApkParseError("binary XML attribute exceeds its chunk")
                    attr_ns, attr_name, raw_index, _attr_size, _res0, data_type = struct.unpack_from(
                        "<IIIHBB", data, position
                    )
                    payload = struct.unpack_from("<I", data, position + 16)[0]
                    key = _string_at(strings, attr_name) or f"<attr-{attr_name}>"
                    element["attributes"][key] = {
                        "namespace": _string_at(strings, attr_ns),
                        "raw": _string_at(strings, raw_index),
                        # TYPE_STRING payloads index the manifest's own string pool.
                        "string": _string_at(strings, payload) if data_type == TYPE_STRING else None,
                        "value": ResValue(data_type, payload),
                    }
            elements.append(element)
        offset += chunk_size
    return elements


class ResourceTable:
    """Resolves resource IDs from ``resources.arsc`` to values and densities."""

    def __init__(self, data: bytes) -> None:
        if len(data) < 12 or struct.unpack_from("<H", data, 0)[0] != CHUNK_TABLE:
            raise ApkParseError("not a resource table")
        header_size = struct.unpack_from("<H", data, 2)[0]
        total_size = min(struct.unpack_from("<I", data, 4)[0], len(data))
        self.strings: list[str] = []
        # (package_id, type_id, entry_index) -> [(density, ResValue)]
        self.entries: dict[tuple[int, int, int], list[tuple[int, ResValue]]] = {}
        self.type_names: dict[tuple[int, int], str] = {}
        offset = header_size
        while offset + 8 <= total_size:
            chunk_type, _header, chunk_size = struct.unpack_from("<HHI", data, offset)
            if chunk_size < 8 or offset + chunk_size > total_size:
                break
            if chunk_type == CHUNK_STRING_POOL:
                self.strings = _read_string_pool(data[offset:offset + chunk_size])
            elif chunk_type == CHUNK_TABLE_PACKAGE:
                self._read_package(data[offset:offset + chunk_size])
            offset += chunk_size

    def _read_package(self, data: bytes) -> None:
        if len(data) < 288:
            return
        package_id = struct.unpack_from("<I", data, 8)[0]
        type_strings_offset = struct.unpack_from("<I", data, 268)[0]
        key_strings_offset = struct.unpack_from("<I", data, 276)[0]
        type_strings: list[str] = []
        if 0 < type_strings_offset < len(data):
            try:
                type_strings = _read_string_pool(data[type_strings_offset:])
            except (ApkParseError, struct.error):
                type_strings = []
        header_size = struct.unpack_from("<H", data, 2)[0]
        chunk_size = struct.unpack_from("<I", data, 4)[0]
        size = min(chunk_size, len(data))
        offset = header_size
        while offset + 8 <= size:
            chunk_type, _chunk_header, entry_chunk_size = struct.unpack_from("<HHI", data, offset)
            if entry_chunk_size < 8 or offset + entry_chunk_size > size:
                break
            if chunk_type == CHUNK_TABLE_TYPE:
                self._read_type(data[offset:offset + entry_chunk_size], package_id, type_strings)
            offset += entry_chunk_size
        _ = key_strings_offset

    def _read_type(self, data: bytes, package_id: int, type_strings: list[str]) -> None:
        if len(data) < 20:
            return
        header_size = struct.unpack_from("<H", data, 2)[0]
        type_id = data[8]
        sparse = bool(data[9] & 0x01)
        entry_count, entries_start = struct.unpack_from("<II", data, 12)
        # The entry offset array starts after the header (which embeds ResTable_config);
        # entries_start is already absolute within this chunk, offsets are relative to it.
        config_size = struct.unpack_from("<I", data, 20)[0] if len(data) >= 24 else 0
        density = 0
        if config_size >= 16 and len(data) >= 36:
            density = struct.unpack_from("<H", data, 34)[0]
        if type_id not in [key[1] for key in self.type_names]:
            name = type_strings[type_id - 1] if 0 < type_id <= len(type_strings) else str(type_id)
            self.type_names[(package_id, type_id)] = name
        if header_size + entry_count * 4 > len(data):
            return
        for index in range(entry_count):
            raw_offset = struct.unpack_from("<I", data, header_size + index * 4)[0]
            if sparse:
                # ResTable_sparseTypeEntry packs the real index and a 4-byte-aligned offset.
                entry_index = raw_offset >> 16
                entry_offset = (raw_offset & 0xFFFF) * 4
            else:
                entry_index = index
                entry_offset = raw_offset
            if entry_offset == 0xFFFFFFFF:
                continue
            position = entries_start + entry_offset
            if position + 16 > len(data):
                continue
            entry_size, flags, _key = struct.unpack_from("<HHI", data, position)
            if entry_size < 8:
                continue
            if flags & FLAG_COMPLEX:
                continue
            value = _read_res_value(data, position + 8)
            self.entries.setdefault((package_id, type_id, entry_index), []).append((density, value))

    def type_name(self, resource_id: int) -> str:
        return self.type_names.get((resource_id >> 24, (resource_id >> 16) & 0xFF), "")

    def resolve(self, resource_id: int, seen: set[int] | None = None) -> tuple[str | None, int]:
        """Resolve a resource ID to a string value and the density that produced it."""
        seen = seen or set()
        if resource_id in seen or resource_id & 0xFF000000 == 0:
            return None, 0
        seen.add(resource_id)
        for density, value in self.resolve_all(resource_id):
            if value.data_type == TYPE_STRING:
                if value.data < len(self.strings):
                    return self.strings[value.data], density
                return None, density
            if value.data_type == TYPE_REFERENCE:
                resolved, inner_density = self.resolve(value.data, seen)
                if resolved:
                    return resolved, inner_density or density
        return None, 0

    def resolve_all(self, resource_id: int, seen: set[int] | None = None) -> list[tuple[int, ResValue]]:
        """Return every density-flavoured value registered for a resource ID."""
        if resource_id & 0xFF000000 == 0:
            return []
        key = (resource_id >> 24, (resource_id >> 16) & 0xFF, resource_id & 0xFFFF)
        candidates = list(self.entries.get(key, []))
        expanded: list[tuple[int, ResValue]] = []
        seen = seen if seen is not None else set()
        for density, value in candidates:
            if value.data_type == TYPE_REFERENCE and value.data not in seen:
                seen.add(value.data)
                expanded.extend(self.resolve_all(value.data, seen))
        return sorted(
            [*candidates, *expanded],
            key=lambda item: DENSITY_SCALE.get(item[0], item[0]),
            reverse=True,
        )


def _attribute(node: dict, name: str) -> ResValue | None:
    attribute = node.get("attributes", {}).get(name)
    return attribute["value"] if attribute else None


def _attribute_int(node: dict, name: str) -> int | None:
    value = _attribute(node, name)
    if value is None:
        return None
    return value.data


def _attribute_text(node: dict, name: str) -> str | None:
    attribute = node.get("attributes", {}).get(name)
    if not attribute:
        return None
    if attribute.get("string"):
        return attribute["string"]
    if attribute.get("raw"):
        return attribute["raw"]
    value = attribute["value"]
    return str(value.data) if value.data_type in (TYPE_INT_DEC, TYPE_INT_HEX) else None


def _manifest_root(elements: list[dict]) -> dict:
    for element in elements:
        if element.get("name") == "manifest":
            return element
    raise ApkParseError("binary manifest has no manifest element")


def _version_code(node: dict) -> str:
    value = _attribute(node, "versionCode")
    if value is None:
        return "0"
    if value.data_type in (TYPE_INT_DEC, TYPE_INT_HEX):
        return str(value.data)
    attribute = node["attributes"]["versionCode"]
    return str(attribute.get("raw") or value.data)


def _uses_sdk(elements: list[dict]) -> dict:
    for element in elements:
        if element.get("name") == "uses-sdk":
            minimum = _attribute_int(element, "minSdkVersion")
            target = _attribute_int(element, "targetSdkVersion")
            return {"minSdk": minimum, "targetSdk": target}
    return {"minSdk": None, "targetSdk": None}


def _device_classes(elements: list[dict]) -> list[str]:
    features: dict[str, bool] = {}
    for element in elements:
        if element.get("name") != "uses-feature":
            continue
        name_value = _attribute_text(element, "name")
        if not name_value:
            continue
        required = _attribute_int(element, "required")
        features[name_value] = True if required is None else bool(required)
    touchscreen = features.get("android.hardware.touchscreen", True)
    leanback = features.get("android.software.leanback", False)
    devices: list[str] = []
    if touchscreen is False or leanback:
        devices.append("tv")
    if not (leanback and touchscreen is False):
        devices.extend(["phone", "pad"])
    return devices or ["phone", "pad"]


def manifest_metadata(manifest: bytes) -> dict:
    """Extract package metadata from an APK's binary AndroidManifest.xml."""
    elements = parse_axml(manifest)
    root = _manifest_root(elements)
    package_value = _attribute_text(root, "package")
    if not package_value:
        raise ApkParseError("manifest has no package name")
    version_name = _attribute_text(root, "versionName")
    permissions = sorted({
        name
        for element in elements
        if element.get("name") == "uses-permission"
        for name in [_attribute_text(element, "name")]
        if name
    })
    application_icon = None
    application_round_icon = None
    for element in elements:
        if element.get("name") == "application":
            icon = _attribute(element, "icon")
            round_icon = _attribute(element, "roundIcon")
            application_icon = icon.data if icon and icon.data_type == TYPE_REFERENCE else None
            application_round_icon = round_icon.data if round_icon and round_icon.data_type == TYPE_REFERENCE else None
            break
    sdk = _uses_sdk(elements)
    return {
        "packageName": package_value,
        "versionName": version_name or "",
        "versionCode": _version_code(root),
        "minSdk": sdk["minSdk"],
        "targetSdk": sdk["targetSdk"],
        "devices": _device_classes(elements),
        "permissions": permissions,
        "iconResource": application_icon,
        "roundIconResource": application_round_icon,
    }


def _drawable_reference(data: bytes) -> list[int]:
    """Collect ``android:drawable`` references from a drawable XML document."""
    try:
        elements = parse_axml(data)
    except (ApkParseError, struct.error):
        return []
    references: list[int] = []
    for element in elements:
        value = _attribute(element, "drawable")
        if value and value.data_type == TYPE_REFERENCE:
            references.append(value.data)
    return references


def _density_of(path: str) -> int:
    match = re.search(r"-(ldpi|mdpi|hdpi|xhdpi|xxhdpi|xxxhdpi|tvdpi|nodpi|anydpi)", path, re.I)
    if not match:
        return 160
    suffix = match.group(1).lower()
    if suffix.endswith("dpi") and suffix not in ("nodpi", "anydpi"):
        return {
            "ldpi": 120, "mdpi": 160, "tvdpi": 213, "hdpi": 240,
            "xhdpi": 320, "xxhdpi": 480, "xxxhdpi": 640,
        }.get(suffix, 160)
    return 160


def _is_raster(path: str) -> bool:
    return path.lower().endswith((".png", ".webp", ".jpg", ".jpeg"))


def extract_launcher_icon(archive: zipfile.ZipFile, table: ResourceTable | None, metadata: dict) -> bytes | None:
    """Return PNG bytes for the best available launcher icon, if any can be decoded."""
    names = set(archive.namelist())
    candidates: list[tuple[int, str]] = []
    adaptive_layers: list[str] = []

    def resolve_paths(resource_id: int | None) -> list[tuple[int, str]]:
        if not resource_id or table is None:
            return []
        resolved: list[tuple[int, str]] = []
        for density, value in table.resolve_all(resource_id):
            if value.data_type != TYPE_STRING or value.data >= len(table.strings):
                continue
            path = table.strings[value.data]
            if path in names:
                resolved.append((max(density, _density_of(path)), path))
        return resolved

    def consider(resource_id: int | None) -> None:
        if not resource_id or table is None:
            return
        for density, path in resolve_paths(resource_id):
            if path.lower().endswith(".xml"):
                try:
                    layer_ids = _drawable_reference(archive.read(path))
                except (KeyError, zipfile.BadZipFile, RuntimeError):
                    continue
                for layer_id in layer_ids:
                    for _layer_density, layer_path in resolve_paths(layer_id):
                        # Prefer a bitmapped layer that is not itself another XML wrapper.
                        if _is_raster(layer_path):
                            adaptive_layers.append(layer_path)
                continue
            if _is_raster(path):
                candidates.append((density, path))

    consider(metadata.get("iconResource"))

    if not candidates:
        # Adaptive icons are layered; the foreground alone loses the background.
        # A bitmapped launcher asset with the same resource name is the better icon.
        foreground_names = {Path(path).stem for path in adaptive_layers}
        for name in names:
            if not _is_raster(name) or not foreground_names:
                continue
            if Path(name).stem in foreground_names:
                candidates.append((_density_of(name) + 1, name))

    if not candidates and not adaptive_layers:
        for name in names:
            lowered = name.lower()
            if not _is_raster(name) or "/" not in name:
                continue
            if re.search(r"/(ic_launcher|app_icon|launcher)[^/]*\.(png|webp|jpg|jpeg)$", lowered):
                candidates.append((_density_of(name), name))

    ordered = [path for _density, path in sorted(candidates, key=lambda item: item[0], reverse=True)]
    ordered.extend(path for path in adaptive_layers if path not in ordered)

    for path in ordered:
        try:
            raw = archive.read(path)
        except (KeyError, zipfile.BadZipFile, RuntimeError):
            continue
        png = to_png(raw)
        if png:
            return png
    return None


def to_png(image: bytes) -> bytes | None:
    """Return PNG bytes for an icon, converting WebP/JPEG when Pillow is available."""
    if image.startswith(b"\x89PNG\r\n\x1a\n"):
        return image
    try:
        from PIL import Image  # imported lazily so the generator keeps working without Pillow
    except ImportError:
        return None
    import io

    try:
        with Image.open(io.BytesIO(image)) as picture:
            converted = picture.convert("RGBA")
            buffer = io.BytesIO()
            converted.save(buffer, format="PNG", optimize=True)
            return buffer.getvalue()
    except Exception:
        return None


def apk_metadata(archive: zipfile.ZipFile) -> dict:
    """Extract package metadata and an optional launcher icon from an open APK."""
    try:
        manifest = archive.read("AndroidManifest.xml")
    except KeyError as error:
        raise ApkParseError("APK has no AndroidManifest.xml") from error
    metadata = manifest_metadata(manifest)
    table: ResourceTable | None = None
    try:
        table = ResourceTable(archive.read("resources.arsc"))
    except (KeyError, ApkParseError, struct.error):
        table = None
    metadata["iconPNG"] = extract_launcher_icon(archive, table, metadata)
    if table is not None:
        metadata["iconResourceName"] = table.type_name(metadata.get("iconResource") or 0)
    return metadata
