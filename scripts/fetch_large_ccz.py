"""
Downloads the "large" QA CCZs (>50MB - over or close to GitHub's 100MB
per-file push limit) into resources/ on demand, instead of committing them.
Small CCZs stay committed directly in resources/ - see resources/README.md
for the full split and how each app_id below was found (each Master Plan
tab's own "Application" HQ link).

Usage:
    python scripts/fetch_large_ccz.py               # fetch anything missing
    python scripts/fetch_large_ccz.py --force        # re-download even if present
    python scripts/fetch_large_ccz.py --web-user      # use HQ_WEB_USER_EMAIL/PASSWORD
                                                       # instead of HQ_API_USERNAME/PASSWORD
"""
import argparse
import os
import pathlib
import sys

sys.path.insert(0, os.path.dirname(__file__))
import hq_client as hqc

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
RESOURCES_DIR = REPO_ROOT / "resources"

# filename PREFIX (HQ's own "{domain} - {app name} - v{version}.ccz" naming,
# minus the version, so a re-fetch after a new release doesn't collide with
# the stale one already on disk - see download_ccz()'s
# _filename_from_content_disposition) -> app_id.
LARGE_CCZS = {
    "qateam - Multimedia": "4df9b7f7e66740a2bd9e02371af832b1",
    "qateam - Right to Left Tests!": "1abba0dead4daede49abc56c04e56ae0",
    "qateam - Mobile Updates - Test 1_2!": "424db1b7c64a94e3e4cdc03c6cc61038",
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true", help="Re-download even if the file already exists.")
    parser.add_argument("--web-user", action="store_true",
                         help="Log in with HQ_WEB_USER_EMAIL/HQ_WEB_USER_PASSWORD instead of "
                              "HQ_API_USERNAME/HQ_API_PASSWORD.")
    parser.add_argument("--include-unreleased", action="store_true",
                         help="Allow the newest build even if not yet Released.")
    args = parser.parse_args()

    from dotenv import load_dotenv
    load_dotenv(REPO_ROOT / ".env")

    RESOURCES_DIR.mkdir(exist_ok=True)
    client = hqc.HQClient()
    if args.web_user:
        client.login(username=os.environ.get("HQ_WEB_USER_EMAIL"), password=os.environ.get("HQ_WEB_USER_PASSWORD"))
    else:
        client.login()

    for prefix, app_id in LARGE_CCZS.items():
        existing = list(RESOURCES_DIR.glob(f"{prefix}*.ccz"))
        if existing and not args.force:
            print(f"skip (already present): {existing[0].name}")
            continue
        print(f"downloading {prefix}... (app {app_id}) ...")
        dest = pathlib.Path(client.download_latest_ccz(
            app_id, str(RESOURCES_DIR), released_only=not args.include_unreleased
        ))
        print(f"  -> {dest.name} ({dest.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
