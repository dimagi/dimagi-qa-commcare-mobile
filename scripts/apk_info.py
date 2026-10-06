"""
What to call a custom (--apk) build in reports and Slack.

A custom APK has no release tag, so the report used to show only its file name
(e.g. "commcare-android-run-34471504203-commcare-release-apk.apk (custom)") -
which says nothing about the VERSION. This reads versionName straight out of the
APK's compiled AndroidManifest.xml (no aapt/Android SDK needed) and, when the APK
was fetched from a commcare-android Actions run by scripts/fetch_apk.py, adds the
branch and run id recorded in the sidecar file fetch_apk.py writes next to it:

    "2.65 · commcare_2.65 · run 37269114433 (custom)"

Anything unreadable falls back to the old "<file name> (custom)" so a reporting
detail can never fail a test run.
"""
import json
import pathlib
import struct
import zipfile

_RES_XML_START_ELEMENT = 0x0102
_RES_STRING_POOL = 0x0001
_UTF8_FLAG = 0x100


def sidecar_path(apk_path):
    return pathlib.Path(str(apk_path) + ".json")


def _read_string_pool(data, offset):
    _, header_size, chunk_size, count, _styles, flags, strings_start, _styles_start = struct.unpack_from(
        "<HHIIIIII", data, offset)
    offsets = struct.unpack_from(f"<{count}I", data, offset + header_size)
    base = offset + strings_start
    utf8 = bool(flags & _UTF8_FLAG)
    strings = []
    for off in offsets:
        pos = base + off
        if utf8:
            # char length (1-2 bytes), then byte length (1-2 bytes), then the bytes
            n = data[pos]
            pos += 2 if n & 0x80 else 1
            blen = data[pos]
            pos += 1
            if blen & 0x80:
                blen = ((blen & 0x7F) << 8) | data[pos]
                pos += 1
            strings.append(data[pos:pos + blen].decode("utf-8", "replace"))
        else:
            (n,) = struct.unpack_from("<H", data, pos)
            pos += 2
            if n & 0x8000:
                (low,) = struct.unpack_from("<H", data, pos)
                n = ((n & 0x7FFF) << 16) | low
                pos += 2
            strings.append(data[pos:pos + n * 2].decode("utf-16-le", "replace"))
    return strings


def read_manifest_attrs(apk_path):
    """Return {'package', 'versionName', 'versionCode'} read from the APK's binary manifest
    (any missing value is None). Raises on a file that is not a readable APK."""
    with zipfile.ZipFile(apk_path) as z:
        data = z.read("AndroidManifest.xml")
    _, header_size, total = struct.unpack_from("<HHI", data, 0)
    pos, strings = header_size, []
    result = {"package": None, "versionName": None, "versionCode": None}
    while pos < min(total, len(data)):
        ctype, chdr, csize = struct.unpack_from("<HHI", data, pos)
        if ctype == _RES_STRING_POOL:
            strings = _read_string_pool(data, pos)
        elif ctype == _RES_XML_START_ELEMENT:
            body = pos + chdr  # after the 16-byte node header
            _ns, name_idx, attr_start, attr_size, attr_count = struct.unpack_from("<iiHHH", data, body)
            if strings[name_idx] == "manifest":
                attrs = body + attr_start
                for i in range(attr_count):
                    _ans, aname, raw, _vsize, _res0, vtype, vdata = struct.unpack_from(
                        "<iiiHBBI", data, attrs + i * attr_size)
                    key = strings[aname]
                    if key in ("package", "versionName") and raw >= 0:
                        result[key] = strings[raw]
                    elif key == "versionCode":
                        result[key] = vdata
                return result
        if csize <= 0:
            break
        pos += csize
    return result


def write_sidecar(apk_path, **extra):
    """Record where a fetched APK came from (and its manifest version) next to the file."""
    info = {k: v for k, v in read_manifest_attrs(apk_path).items() if v is not None}
    info.update({k: v for k, v in extra.items() if v})
    sidecar_path(apk_path).write_text(json.dumps(info, indent=2), encoding="utf-8")
    return info


def describe_custom_apk(apk_path):
    """Text for reports/apk_version.txt (and so the Slack line) for a custom APK."""
    name = pathlib.Path(apk_path).name
    try:
        sidecar = sidecar_path(apk_path)
        info = json.loads(sidecar.read_text(encoding="utf-8")) if sidecar.exists() else {}
        if not info.get("versionName"):
            info.update({k: v for k, v in read_manifest_attrs(apk_path).items() if v is not None})
    except Exception:
        return f"{name} (custom)"
    parts = [info.get("versionName")]
    if info.get("branch"):
        parts.append(info["branch"])
    if info.get("run_id"):
        parts.append(f"run {info['run_id']}")
    parts = [p for p in parts if p]
    if not parts:
        return f"{name} (custom)"
    if not info.get("run_id"):
        parts.append(name)  # e.g. a committed resources/*.apk: keep the file name as well
    return " · ".join(parts) + " (custom)"


if __name__ == "__main__":
    import sys
    for path in sys.argv[1:]:
        print(path, "->", read_manifest_attrs(path), "|", describe_custom_apk(path))
