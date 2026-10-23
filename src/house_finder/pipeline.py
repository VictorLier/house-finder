"""Download OSM data and build the distance grids used by the app."""

import argparse
import shutil
import urllib.request

from . import build_grid, config, extract


def download(force: bool = False):
    if config.PBF_PATH.exists() and not force:
        print(f"Using existing {config.PBF_PATH} (pass --refresh to re-download)")
        return
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = config.PBF_PATH.with_suffix(".part")
    print(f"Downloading {config.PBF_URL} (~500 MB)...")
    req = urllib.request.Request(config.PBF_URL, headers={"User-Agent": "house-finder/0.1"})
    with urllib.request.urlopen(req) as resp, open(tmp, "wb") as f:
        shutil.copyfileobj(resp, f, length=1 << 20)
    tmp.replace(config.PBF_PATH)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--refresh", action="store_true",
                        help="re-download the latest OpenStreetMap extract")
    parser.add_argument("--remove-sources", action="store_true",
                        help="delete the OSM download and intermediate features afterwards "
                             "(saves ~570 MB; used in Docker)")
    args = parser.parse_args()
    download(force=args.refresh)
    extract.main()
    build_grid.main()
    if args.remove_sources:
        for path in (config.PBF_PATH, config.FEATURES_PATH):
            path.unlink(missing_ok=True)
        print("Removed the OSM download and intermediate features.")
    print("Done. Start the app with: uv run house-finder")


if __name__ == "__main__":
    main()
