"""Unit tests for scripts/download_apk.py's release picking and version check.

Run: python -m pytest tests/
No network: list_releases() and the APK manifest reader are stubbed.
"""
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))

import apk_info  # noqa: E402
import download_apk  # noqa: E402


def release(tag, asset="app-commcare-release.apk", draft=False, prerelease=False):
    return {
        "tag_name": tag,
        "draft": draft,
        "prerelease": prerelease,
        "assets": [{"name": asset, "size": 1, "browser_download_url": f"https://example.invalid/{tag}/{asset}"}],
    }


def test_tag_version():
    assert download_apk.tag_version("commcare_2.65.0") == (2, 65, 0)
    assert download_apk.tag_version("commcare_2.61.5test") is None
    assert download_apk.tag_version(None) is None


def test_version_tuple_ignores_trailing_zeros():
    assert download_apk._version_tuple("2.65") == download_apk._version_tuple("2.65.0")
    assert download_apk._version_tuple("2.65.1") != download_apk._version_tuple("2.65")


def test_resolve_skips_draft_prerelease_and_test_tags(monkeypatch):
    monkeypatch.setattr(download_apk, "list_releases", lambda: [
        release("commcare_2.66.0", draft=True),
        release("commcare_2.65.1", prerelease=True),
        release("commcare_2.61.5test", asset="commcare_2.61.5test.apk"),
        release("commcare_2.65.0"),
    ])
    picked, _ = download_apk.resolve()
    assert picked["tag_name"] == "commcare_2.65.0"


def test_resolve_picks_highest_version_not_newest_upload(monkeypatch):
    # The API lists newest-published first: an older patch uploaded after 2.65.0.
    monkeypatch.setattr(download_apk, "list_releases", lambda: [
        release("commcare_2.64.2"),
        release("commcare_2.65.0"),
    ])
    picked, _ = download_apk.resolve()
    assert picked["tag_name"] == "commcare_2.65.0"


def test_resolve_skips_release_without_usable_apk(monkeypatch):
    monkeypatch.setattr(download_apk, "list_releases", lambda: [
        release("commcare_2.65.1", asset="app-commcare-release.aab"),
        release("commcare_2.65.0"),
    ])
    picked, _ = download_apk.resolve()
    assert picked["tag_name"] == "commcare_2.65.0"


def test_check_version_passes_on_match(monkeypatch):
    monkeypatch.setattr(apk_info, "read_manifest_attrs", lambda path: {"versionName": "2.65"})
    assert download_apk.check_version("x.apk", release("commcare_2.65.0")) == "2.65"


def test_check_version_exits_on_mismatch(monkeypatch):
    monkeypatch.setattr(apk_info, "read_manifest_attrs", lambda path: {"versionName": "2.64.1"})
    with pytest.raises(SystemExit, match="version mismatch"):
        download_apk.check_version("x.apk", release("commcare_2.65.0"))


def test_check_version_exits_when_unreadable(monkeypatch):
    monkeypatch.setattr(apk_info, "read_manifest_attrs", lambda path: {})
    with pytest.raises(SystemExit, match="Couldn't read versionName"):
        download_apk.check_version("x.apk", release("commcare_2.65.0"))
