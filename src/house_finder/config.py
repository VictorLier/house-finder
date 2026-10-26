"""Shared configuration: paths, raster grid and feature categories."""

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
# Override with HOUSE_FINDER_DATA (e.g. a mounted volume in Docker).
DATA_DIR = Path(os.environ.get("HOUSE_FINDER_DATA") or ROOT / "data").resolve()
PBF_URL = "https://download.geofabrik.de/europe/denmark-latest.osm.pbf"
PBF_PATH = DATA_DIR / "denmark-latest.osm.pbf"
PROCESSED_DIR = DATA_DIR / "processed"
FEATURES_PATH = PROCESSED_DIR / "features.pkl"
GRID_DIR = PROCESSED_DIR / "grid"
POINTS_PATH = PROCESSED_DIR / "points.json"
HOUSES_PATH = PROCESSED_DIR / "houses.json"

# Houses for sale (Boligsiden's unofficial API). Fetched only on request and
# re-used for this long before another download is allowed.
HOUSES_API = "https://api.boligsiden.dk/search/cases"
HOUSES_MAX_AGE_HOURS = 24
HOUSES_REQUEST_DELAY_S = 1.0

# Raster cell size in Web Mercator metres. True size is this * cos(latitude),
# i.e. roughly 85-92 m across Denmark.
CELL_SIZE_MERC = 160.0

# Distances are stored as uint16 metres; anything farther is clamped.
MAX_STORED_DISTANCE = 65_000

# Lake size classes (hectares) available in the GUI.
LAKE_MIN_AREAS_HA = (5, 20, 100)

NATURE_TYPES = {
    "forest": "Woods / forest",
    "heath": "Heath",
    "reserve": "Nature reserves & national parks",
    "park": "Parks",
    "beach": "Beaches",
}

STATION_TYPES = {
    "train": "Train (incl. S-tog & local trains)",
    "metro": "Metro",
    "light_rail": "Light rail (letbane)",
}

INSTITUTION_TYPES = {
    "school": "Schools",
    "daycare": "Daycare & kindergartens",
    "higher_education": "Colleges & universities",
    "healthcare": "Healthcare facilities",
    "library": "Libraries",
}
