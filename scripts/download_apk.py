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
# Matches "app-commcare-release.apk" and "commcare-2.63.1-release.apk" alike;
# excludes lts/staging/connect variants which are separate build flavors.
APK_PATTERN = re.compile(r"^(app-)?commcare[-_].*release\.apk$", re.IGNORECASE)
MAX_RELEASES_TO_SCAN = 15


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

    for release in list_releases():
        asset = find_asset(release)
        if asset:
            return release, asset
    raise SystemExit(f"No release in the last {MAX_RELEASES_TO_SCAN} releases has a usable .apk asset.")


def download(url, out_path, expected_size=None):
    """`expected_size` (an asset dict's own `size` field, in bytes): if
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
        return
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    urllib.request.urlretrieve(url, out_path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", default=os.environ.get("COMMCARE_RELEASE_TAG") or None,
                         help="GitHub release tag, e.g. commcare_2.63.4. Defaults to the "
                              "most recent release that has a usable apk asset.")
    parser.add_argument("--out", default="apks/app-commcare-release.apk",
                         help="Where to save the downloaded APK.")
    args = parser.parse_args()

    release, asset = resolve(args.tag)
    print(f"Downloading {asset['name']} from release {release['tag_name']} "
          f"({asset['size']} bytes) ...")
    download(asset["browser_download_url"], args.out, expected_size=asset["size"])
    print(f"Saved to {args.out}")
    print(f"::set-output name=apk_path::{args.out}")  # harmless outside GH Actions
    print(f"::set-output name=release_tag::{release['tag_name']}")


if __name__ == "__main__":
    main()
