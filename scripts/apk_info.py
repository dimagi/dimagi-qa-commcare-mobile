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
import glob
import json
import os
import pathlib
import shutil
import struct
import subprocess
import tempfile
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


# --------------------------------------------------------------------------- repackaging
def find_build_tool(name):
    """Locate an Android SDK build-tool (apksigner, zipalign) or JDK tool (keytool): PATH first,
    then ANDROID_HOME / ANDROID_SDK_ROOT / the usual per-OS SDK folders (GitHub's ubuntu runners
    keep it under /usr/local/lib/android/sdk). Returns a path or None."""
    exts = ("", ".bat", ".exe") if os.name == "nt" else ("",)
    for ext in exts:
        found = shutil.which(name + ext)
        if found:
            return found
    roots = [os.environ.get("ANDROID_HOME"), os.environ.get("ANDROID_SDK_ROOT"),
             os.path.join(os.environ.get("LOCALAPPDATA", ""), "Android", "Sdk"),
             "/usr/local/lib/android/sdk", os.path.expanduser("~/Android/Sdk")]
    for root in filter(None, roots):
        candidates = []
        for ext in exts:
            candidates += glob.glob(os.path.join(root, "build-tools", "*", name + ext))
        if candidates:
            return sorted(candidates, key=lambda c: [int(x) if x.isdigit() else 0 for x in
                                                     pathlib.Path(c).parent.name.split(".")])[-1]
    return None


def patch_version_code(apk_in, apk_out, version_code):
    """Copy apk_in to apk_out with the manifest's versionCode set to `version_code` and the old
    signature files removed (the result is UNSIGNED - sign it afterwards). Patches the 4-byte
    value in the compiled manifest in place; nothing else about the app changes."""
    with zipfile.ZipFile(apk_in) as z:
        manifest = bytearray(z.read("AndroidManifest.xml"))
    _, header_size, total = struct.unpack_from("<HHI", manifest, 0)
    pos, strings, patched = header_size, [], False
    while pos < min(total, len(manifest)) and not patched:
        ctype, chdr, csize = struct.unpack_from("<HHI", manifest, pos)
        if ctype == _RES_STRING_POOL:
            strings = _read_string_pool(bytes(manifest), pos)
        elif ctype == _RES_XML_START_ELEMENT:
            body = pos + chdr
            _ns, name_idx, attr_start, attr_size, attr_count = struct.unpack_from("<iiHHH", manifest, body)
            if strings[name_idx] == "manifest":
                for i in range(attr_count):
                    off = body + attr_start + i * attr_size
                    _ans, aname = struct.unpack_from("<ii", manifest, off)
                    if strings[aname] == "versionCode":
                        struct.pack_into("<I", manifest, off + 16, version_code)  # typed value's data field
                        patched = True
                break
        if csize <= 0:
            break
        pos += csize
    if not patched:
        raise RuntimeError(f"no versionCode attribute found in {apk_in}'s manifest")
    with zipfile.ZipFile(apk_in) as zin, zipfile.ZipFile(apk_out, "w") as zout:
        for item in zin.infolist():
            if item.filename.startswith("META-INF/") and item.filename.upper().endswith((".SF", ".RSA", ".DSA", ".EC", ".MF")):
                continue  # old signature; invalid after the patch
            data = bytes(manifest) if item.filename == "AndroidManifest.xml" else zin.read(item.filename)
            zout.writestr(item, data, compress_type=item.compress_type)
    return apk_out


def _run(cmd, **kw):
    proc = subprocess.run(cmd, capture_output=True, text=True, **kw)
    if proc.returncode != 0:
        raise RuntimeError(f"{pathlib.Path(cmd[0]).name} failed: {(proc.stderr or proc.stdout).strip()[:300]}")
    return proc.stdout


def _sign(apk_unsigned, apk_signed, signer_args):
    zipalign, apksigner = find_build_tool("zipalign"), find_build_tool("apksigner")
    if not (zipalign and apksigner):
        raise RuntimeError("zipalign/apksigner (Android SDK build-tools) not found")
    aligned = str(apk_signed) + ".aligned"
    _run([zipalign, "-f", "-p", "4", str(apk_unsigned), aligned])
    _run([apksigner, "sign", *signer_args, "--out", str(apk_signed), aligned])
    os.remove(aligned)
    _run([apksigner, "verify", str(apk_signed)])
    return apk_signed


def _make_signer_args(work):
    """A throwaway signing identity for apksigner: a JDK keystore when keytool exists (GitHub's
    runners), else a key + self-signed certificate made with the `cryptography` package."""
    keytool = find_build_tool("keytool")
    if keytool:
        password = "qa-" + os.urandom(6).hex()
        keystore = work / "qa-repack.keystore"
        _run([keytool, "-genkeypair", "-keystore", str(keystore), "-storepass", password,
              "-keypass", password, "-alias", "qa", "-keyalg", "RSA", "-keysize", "2048",
              "-validity", "3650", "-dname", "CN=QA upgrade-test repack"])
        return ["--ks", str(keystore), "--ks-pass", f"pass:{password}", "--ks-key-alias", "qa"]
    try:
        import datetime
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID
    except ImportError as exc:
        raise RuntimeError("neither keytool (JDK) nor the `cryptography` package is available") from exc
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "QA upgrade-test repack")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - datetime.timedelta(days=1))
            .not_valid_after(now + datetime.timedelta(days=3650)).sign(key, hashes.SHA256()))
    key_path, cert_path = work / "qa-repack.pk8", work / "qa-repack.pem"
    key_path.write_bytes(key.private_bytes(serialization.Encoding.DER, serialization.PrivateFormat.PKCS8,
                                           serialization.NoEncryption()))
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return ["--key", str(key_path), "--cert", str(cert_path)]


def repackage_for_upgrade(old_apk, new_apk, work_dir=None):
    """Make an in-place upgrade from `old_apk` to `new_apk` possible when it otherwise isn't.

    Android upgrades an installed app only if the new APK has the SAME signing key and a HIGHER
    versionCode. A dev/PR build from commcare-android (e.g. the 2.65 qaAutomation build) is signed
    with the release key but has versionCode 1 - lower than the 2.45 binary - so the swap is
    rejected. We cannot re-sign with Dimagi's release key, so for the TEST both APKs are re-signed
    with one throwaway key and the new one gets versionCode = old + 1. The app's code and
    resources are untouched. Returns (old_signed, new_signed, description); raises RuntimeError
    (with the reason) if the tools are unavailable or anything fails."""
    work = pathlib.Path(work_dir or tempfile.mkdtemp(prefix="repack-"))
    work.mkdir(parents=True, exist_ok=True)
    old_vc = read_manifest_attrs(old_apk)["versionCode"]
    new_vc = read_manifest_attrs(new_apk)["versionCode"]
    bumped = old_vc + 1
    signer_args = _make_signer_args(work)
    old_signed = _sign(_copy_unsigned(old_apk, work / "old.unsigned.apk"),
                       work / pathlib.Path(old_apk).name, signer_args)
    patched = patch_version_code(new_apk, work / "new.unsigned.apk", bumped)
    new_signed = _sign(patched, work / pathlib.Path(new_apk).name, signer_args)
    if read_manifest_attrs(new_signed)["versionCode"] != bumped:
        raise RuntimeError("versionCode patch did not take effect")
    return old_signed, new_signed, (f"re-signed both APKs with a throwaway key and set the new APK's "
                                    f"versionCode {new_vc} -> {bumped} (old is {old_vc})")


def _copy_unsigned(apk_in, apk_out):
    """Old APK: same versionCode, signature files stripped, ready to re-sign."""
    with zipfile.ZipFile(apk_in) as zin, zipfile.ZipFile(apk_out, "w") as zout:
        for item in zin.infolist():
            if item.filename.startswith("META-INF/") and item.filename.upper().endswith((".SF", ".RSA", ".DSA", ".EC", ".MF")):
                continue
            zout.writestr(item, zin.read(item.filename), compress_type=item.compress_type)
    return apk_out


if __name__ == "__main__":
    import sys
    for path in sys.argv[1:]:
        print(path, "->", read_manifest_attrs(path), "|", describe_custom_apk(path))
