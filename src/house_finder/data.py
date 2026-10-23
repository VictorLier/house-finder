"""Memory-mapped access to the distance grids, shared by the GUI and the tile server."""

from __future__ import annotations

import functools
import json
import threading

import numpy as np
from pyproj import Transformer

from . import config

R_EARTH = 6378137.0
_TO_WGS = Transformer.from_crs("EPSG:3857", "EPSG:4326", always_xy=True)
_TO_MERC = Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)

# A criterion is (layer names, max distance in metres); a cell satisfies it when
# its distance to the nearest of those layers is within the limit.
Criterion = tuple[tuple[str, ...], float]
Criteria = tuple[Criterion, ...]


def data_ready() -> bool:
    return (config.GRID_DIR / "meta.json").exists() and config.POINTS_PATH.exists()


class GridData:
    def __init__(self):
        meta = json.loads((config.GRID_DIR / "meta.json").read_text())
        self.x0, self.y1, self.cell = meta["x0"], meta["y1"], meta["cell"]
        self.height, self.width = meta["height"], meta["width"]
        self.version = str(int(meta["built"]))
        self.land = np.load(config.GRID_DIR / "land.npy", mmap_mode="r")
        self.layers = {
            name: np.load(config.GRID_DIR / f"d_{name}.npy", mmap_mode="r")
            for name in meta["layers"]
        }
        row_y = self.y1 - (np.arange(self.height) + 0.5) * self.cell
        row_scale = np.cos(2 * np.arctan(np.exp(row_y / R_EARTH)) - np.pi / 2)
        self.row_area_km2 = (self.cell * row_scale) ** 2 / 1e6
        self.land_area_km2 = float((self.land.sum(axis=1) * self.row_area_km2).sum())
        with open(config.POINTS_PATH, encoding="utf-8") as f:
            self.markers = json.load(f)
        self._lock = threading.Lock()
        self._match_cached = functools.lru_cache(maxsize=4)(self._compute_match)

    @property
    def merc_bounds(self):
        return (self.x0, self.y1 - self.height * self.cell,
                self.x0 + self.width * self.cell, self.y1)

    def cell_of(self, lat: float, lon: float):
        """(row, col) of the grid cell at lat/lon, or None outside the grid."""
        x, y = _TO_MERC.transform(lon, lat)
        col = int(np.floor((x - self.x0) / self.cell))
        row = int(np.floor((self.y1 - y) / self.cell))
        if 0 <= row < self.height and 0 <= col < self.width:
            return row, col
        return None

    def cells_of(self, lats, lons):
        """Vectorised (rows, cols, valid) for arrays of lat/lon."""
        x, y = _TO_MERC.transform(np.asarray(lons, float), np.asarray(lats, float))
        cols = np.floor((np.asarray(x) - self.x0) / self.cell).astype(np.int64)
        rows = np.floor((self.y1 - np.asarray(y)) / self.cell).astype(np.int64)
        valid = (rows >= 0) & (rows < self.height) & (cols >= 0) & (cols < self.width)
        return np.where(valid, rows, 0), np.where(valid, cols, 0), valid

    def criterion_min(self, layers, rows=slice(None), cols=slice(None)) -> np.ndarray:
        d = np.asarray(self.layers[layers[0]][rows, cols])
        for name in layers[1:]:
            d = np.minimum(d, self.layers[name][rows, cols])
        return d

    def match(self, criteria: Criteria) -> np.ndarray:
        """Boolean full-grid mask of Danish land cells meeting all criteria."""
        with self._lock:
            return self._match_cached(criteria)

    def _compute_match(self, criteria: Criteria) -> np.ndarray:
        m = np.array(self.land, dtype=bool)
        for layers, max_m in criteria:
            m &= self.criterion_min(layers) <= max_m
        m.flags.writeable = False
        return m

    def match_area_km2(self, criteria: Criteria) -> float:
        return float((self.match(criteria).sum(axis=1) * self.row_area_km2).sum())


@functools.cache
def get_data() -> GridData:
    return GridData()


def encode_criteria(criteria: Criteria) -> str:
    return ",".join("+".join(layers) + f":{max_m:g}" for layers, max_m in criteria)


def decode_criteria(text: str, known_layers) -> Criteria:
    """Parse and validate the URL form produced by :func:`encode_criteria`."""
    out = []
    for part in filter(None, text.split(",")):
        names, _, dist = part.partition(":")
        layers = tuple(names.split("+"))
        if not layers or any(n not in known_layers for n in layers):
            raise ValueError(f"unknown layer in {part!r}")
        max_m = float(dist)
        if not 0 <= max_m <= config.MAX_STORED_DISTANCE:
            raise ValueError(f"distance out of range in {part!r}")
        out.append((layers, max_m))
    return tuple(out)
