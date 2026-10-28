"""
Fetch the CommCare Android release APK from GitHub releases (dimagi/commcare-android)
so this repo never needs to build or vendor commcare-android itself.

Asset naming is NOT consistent across releases - confirmed against the live releases
API (2026-08-04):
    commcare_2.63.4  -> app-commcare-release.apk
    commcare_2.63.1  -> commcare-2.63.1-release.apk
    commcare_2.63.3  -> no .apk asset at all (aab only)
So this script matches by pattern (main release .apk, excluding lts/staging variants)
rather than a fixed filename, and if a specific/latest release has no matching asset
it walks backward through recent releases to find one that does.

"Latest" means the highest CommCare VERSION among published releases, not the most
recently published one: the release manager uploads releases by hand, and a patch
for an older line can be published after a newer version (Shubham, 2026-10-20).
Drafts, prereleases and tags that aren't a plain version (e.g. commcare_2.61.5test)
are never picked. After downloading, the APK's own versionName must match the
release tag, or the run stops - see check_version().

Usage:
    python scripts/download_apk.py                       # latest release with a usable apk
    python scripts/download_apk.py --tag commcare_2.63.4  # specific release (must have an apk)
    python scripts/download_apk.py --out apks/my.apk
"""
import argparse
import json
import os
import re
import urllib.request

GITHUB_API = "https://api.github.com/repos/dimagi/commcare-android"
# Matches the main CommCare APK under every naming seen so far:
#   app-commcare-release.apk, commcare-2.63.1-release.apk, commcare-2.63.2.apk,
#   app-commcare-release_2.64.1.apk (2.64.1 puts the version AFTER "release" - the
#   old pattern required a trailing "release.apk", so "latest" silently fell back to
#   2.64.0 and an explicit --tag commcare_2.64.1 failed outright), commcare_2.61.5.apk.
# A positive match - "release" and/or a bare version, nothing else - so lts/staging/
# connect flavors and test/debug builds never match: commcare-android's draft
# commcare_2.61.5test (asset commcare_2.61.5test.apk) and names like
# app-commcare-debug.apk / app-commcare-qaAutomation-release.apk are rejected.
APK_PATTERN = re.compile(
    r"^(app-)?commcare[-_](release([-_]\d+(\.\d+)+)?|\d+(\.\d+)+([-_]release)?)\.apk$", re.IGNORECASE)
MAX_RELEASES_TO_SCAN = 15
# commcare_2.65.0 -> (2, 65, 0). Anything else (commcare_2.61.5test) isn't a release.
TAG_VERSION = re.compile(r"^commcare_(\d+(?:\.\d+)*)$")


def tag_version(tag):
    """Version tuple for a release tag, or None if the tag isn't a plain version."""
    match = TAG_VERSION.match(tag or "")
    return tuple(int(part) for part in match.group(1).split(".")) if match else None


def _version_tuple(text):
    """'2.65' and '2.65.0' compare equal: trailing zeros are dropped."""
    parts = [int(part) for part in re.findall(r"\d+", text or "")]
    while parts and parts[-1] == 0:
        parts.pop()
    return tuple(parts)


def _get_json(url):
    req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json"})
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req) as resp:
        return json.load(resp)


def get_release_by_tag(tag):
    return _get_json(f"{GITHUB_API}/releases/tags/{tag}")


def list_releases(per_page=MAX_RELEASES_TO_SCAN):
    return _get_json(f"{GITHUB_API}/releases?per_page={per_page}")


def find_asset(release):
    for asset in release.get("assets", []):
        if APK_PATTERN.match(asset["name"]):
            return asset
    return None


def resolve(tag=None):
    """Return (release, asset). If `tag` is given it must have a usable apk asset
    (fails loudly otherwise, since the caller asked for that exact build). If no
    tag is given, walk recent releases until one has a usable apk asset."""
    if tag:
        release = get_release_by_tag(tag)
        asset = find_asset(release)
        if not asset:
            raise SystemExit(
                f"Release '{tag}' has no release .apk asset. "
                f"Available assets: {[a['name'] for a in release.get('assets', [])]}"
            )
        return release, asset

    candidates = []
    for release in list_releases():
        # Drafts and pre-releases are never "latest": a token that can see drafts (a
        # commcare-android collaborator's) lists the draft commcare_2.61.5test first.
        if release.get("draft") or release.get("prerelease"):
            continue
        version = tag_version(release.get("tag_name"))
        asset = find_asset(release)
        if version and asset:
            candidates.append((version, release, asset))
    if not candidates:
        raise SystemExit(f"No release in the last {MAX_RELEASES_TO_SCAN} releases has a usable .apk asset.")
    _, release, asset = max(candidates, key=lambda candidate: candidate[0])
    return release, asset


def check_version(apk_path, release):
    """Stop the run unless the APK's versionName matches its release tag.

    Returns the APK's versionName. Raises SystemExit on a mismatch, or when the
    version can't be read at all, so a run never reports results for the wrong build.
    """
    import apk_info

    expected = release["tag_name"].removeprefix("commcare_")
    actual = apk_info.read_manifest_attrs(apk_path).get("versionName")
    if not actual:
        raise SystemExit(f"Couldn't read versionName from {apk_path}; refusing to run an unverified APK.")
    if _version_tuple(actual) != _version_tuple(expected):
        raise SystemExit(
            f"APK version mismatch: {apk_path} is versionName {actual}, "
            f"but release {release['tag_name']} should be {expected}."
        )
    print(f"APK version check passed: {actual} matches {release['tag_name']}.")
    return actual


def download(url, out_path, expected_size=None, release=None):
    """`release` (the release dict resolve() returned): when given, the APK's
    versionName is checked against the release tag afterwards - see check_version().

    `expected_size` (an asset dict's own `size` field, in bytes): if
    out_path already exists and matches this size exactly, skips the
    download entirely - confirmed live (2026-08-25) this repo's several
    run_suite.py/run_appium_suite.py invocations within the same CI job (or
    local session) each re-download the same current-release APK from
    scratch, and a real local network hiccup once left a TRUNCATED file on
    disk that a later run silently reused - matching by exact byte size
    catches both: a genuinely-complete prior download is reused instead of
    re-fetched, but any partial/corrupt file (wrong size) is always
    re-downloaded rather than trusted."""
    if expected_size is not None and os.path.exists(out_path) and os.path.getsize(out_path) == expected_size:
        print(f"{out_path} already matches the expected {expected_size} bytes - skipping download.")
    else:
        os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
        urllib.request.urlretrieve(url, out_path)
    if release is not None:
        check_version(out_path, release)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", default=os.environ.get("COMMCARE_RELEASE_TAG") or None,
                         help="GitHub release tag, e.g. commcare_2.63.4. Defaults to the "
                              "highest-version published release that has a usable apk asset.")
    parser.add_argument("--out", default="apks/app-commcare-release.apk",
                         help="Where to save the downloaded APK.")
    args = parser.parse_args()

    release, asset = resolve(args.tag)
    print(f"Downloading {asset['name']} from release {release['tag_name']} "
          f"({asset['size']} bytes) ...")
    download(asset["browser_download_url"], args.out, expected_size=asset["size"], release=release)
    print(f"Saved to {args.out}")
    print(f"::set-output name=apk_path::{args.out}")  # harmless outside GH Actions
    print(f"::set-output name=release_tag::{release['tag_name']}")


if __name__ == "__main__":
    main()
