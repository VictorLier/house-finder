"""Step 2: rasterize features and compute distance grids.

Produces ``grid/`` with one uint16 distance layer (true metres) per feature
type plus a Danish land mask (memory-mappable .npy files), and
``points.json`` with station and harbour markers for the map.
"""

from __future__ import annotations

import json
import pickle
import shutil
import time

import numpy as np
import shapely
from pyproj import Transformer
from rasterio import features as rfeatures
from rasterio.transform import from_origin
from scipy import ndimage

from . import config

_TO_WGS = Transformer.from_crs("EPSG:3857", "EPSG:4326", always_xy=True)
R_EARTH = 6378137.0


def merc_y_to_lat(y):
    return np.degrees(2 * np.arctan(np.exp(np.asarray(y) / R_EARTH)) - np.pi / 2)


class Grid:
    def __init__(self, bounds, cell):
        minx, miny, maxx, maxy = bounds
        self.cell = cell
        self.x0 = np.floor(minx / cell) * cell
        self.y1 = np.ceil(maxy / cell) * cell
        self.width = int(np.ceil((maxx - self.x0) / cell))
        self.height = int(np.ceil((self.y1 - miny) / cell))
        self.transform = from_origin(self.x0, self.y1, cell, cell)
        row_centre_y = self.y1 - (np.arange(self.height) + 0.5) * cell
        # Scale factor from Mercator metres to true metres for each row.
        self.row_scale = np.cos(np.radians(merc_y_to_lat(row_centre_y))).astype(np.float32)

    @property
    def shape(self):
        return (self.height, self.width)

    def rasterize(self, geoms, all_touched=True):
        geoms = [g for g in geoms if g is not None and not g.is_empty]
        if not geoms:
            return np.zeros(self.shape, dtype=bool)
        return rfeatures.rasterize(
            ((g, 1) for g in geoms), out_shape=self.shape, transform=self.transform,
            fill=0, all_touched=all_touched, dtype="uint8",
        ).astype(bool)

    def distance(self, target: np.ndarray) -> np.ndarray:
        """True-metre distance from every cell to the nearest ``target`` cell."""
        if not target.any():
            return np.full(self.shape, config.MAX_STORED_DISTANCE, dtype=np.uint16)
        d = ndimage.distance_transform_edt(~target).astype(np.float32)
        d *= self.cell
        d *= self.row_scale[:, None]
        np.minimum(d, config.MAX_STORED_DISTANCE, out=d)
        return np.rint(d).astype(np.uint16)

    def cell_of(self, x, y):
        col = np.floor((np.asarray(x) - self.x0) / self.cell).astype(int)
        row = np.floor((self.y1 - np.asarray(y)) / self.cell).astype(int)
        return row, col


def build_sea_mask(grid: Grid, coast_lines, territory) -> np.ndarray:
    """Classify cells as sea using OSM coastline orientation.

    OSM coastlines have land on the left and sea on the right. The coastline is
    burned in as a barrier, the remaining cells are split into connected
    components, and each component is labelled land or sea by majority vote of
    sample points placed just left/right of every coastline segment.

    The Danish territory outline is added as an extra barrier because the OSM
    extract truncates the German/Swedish coastline, which would otherwise let
    the Jutland land component leak into the sea.
    """
    coast_barrier = grid.rasterize(coast_lines, all_touched=True)
    border_barrier = grid.rasterize([territory.boundary], all_touched=True) & ~coast_barrier
    barrier = coast_barrier | border_barrier
    labels, n = ndimage.label(~barrier)  # 4-connectivity: barrier cannot leak

    coords = [shapely.get_coordinates(line) for line in coast_lines]
    seg_a = np.concatenate([c[:-1] for c in coords])
    seg_b = np.concatenate([c[1:] for c in coords])
    vec = seg_b - seg_a
    length = np.hypot(vec[:, 0], vec[:, 1])
    ok = length > 0
    mid = (seg_a[ok] + seg_b[ok]) / 2
    normal_left = np.column_stack([-vec[ok, 1], vec[ok, 0]]) / length[ok, None]
    offset = 1.5 * grid.cell

    votes = np.zeros((n + 1, 2), dtype=np.int64)  # [land, sea]
    for side, sign in ((0, 1.0), (1, -1.0)):
        pts = mid + sign * offset * normal_left
        r, c = grid.cell_of(pts[:, 0], pts[:, 1])
        inside = (r >= 0) & (r < grid.height) & (c >= 0) & (c < grid.width)
        lab = labels[r[inside], c[inside]]
        lab = lab[lab > 0]
        votes[:, side] += np.bincount(lab, minlength=n + 1)

    is_sea = votes[:, 1] > votes[:, 0]
    # Components without votes: sea if they touch the grid border, else land.
    no_votes = votes.sum(axis=1) == 0
    border = np.unique(np.concatenate([labels[0], labels[-1], labels[:, 0], labels[:, -1]]))
    touches_border = np.zeros(n + 1, dtype=bool)
    touches_border[border] = True
    is_sea[no_votes] = touches_border[no_votes]
    is_sea[0] = False  # coastline (shoreline) cells count as land
    sea = is_sea[labels]
    # Border-only barrier cells take the class of the nearest regular cell
    # inside Danish territory (the area outside is not reliably classified).
    interior = grid.rasterize([territory], all_touched=False)
    reference = ~barrier & interior
    _, (ri, ci) = ndimage.distance_transform_edt(~reference, return_distances=True,
                                                 return_indices=True)
    sea[border_barrier] = sea[ri[border_barrier], ci[border_barrier]]
    return sea


def build(features_path=config.FEATURES_PATH):
    t0 = time.time()

    def log(msg):
        print(f"[{time.time() - t0:5.0f}s] {msg}", flush=True)

    with open(features_path, "rb") as f:
        feat = pickle.load(f)

    denmark = feat["denmark"]
    grid = Grid(shapely.buffer(denmark, 2_000).bounds, config.CELL_SIZE_MERC)
    log(f"Grid {grid.width} x {grid.height} cells")

    territory = grid.rasterize([denmark], all_touched=True)
    sea = build_sea_mask(grid, feat["coast"], denmark)
    land = ~sea
    land_dk = land & territory
    log(f"Land/sea mask done; Danish land cells: {land_dk.sum():,}")

    layers: dict[str, np.ndarray] = {}
    layers["coast"] = grid.distance(sea)
    log("coast")

    lakes = feat["areas"].get("lake", [])
    lake_area_ha = np.array([
        g.area * np.cos(np.radians(merc_y_to_lat(g.centroid.y))) ** 2 / 10_000 for g in lakes
    ])
    for min_ha in config.LAKE_MIN_AREAS_HA:
        sel = [g for g, a in zip(lakes, lake_area_ha) if a >= min_ha]
        layers[f"lake_{min_ha}"] = grid.distance(grid.rasterize(sel) & territory)
        log(f"lakes >= {min_ha} ha ({len(sel):,} lakes)")

    for nt in config.NATURE_TYPES:
        target = grid.rasterize(feat["areas"].get("nature_" + nt, []))
        pts = [g for g, _ in feat["points"].get("nature_" + nt, [])]
        if pts:
            target |= grid.rasterize(pts)
        # Ignore the sea-covered parts of reserves/national parks (e.g. Vadehavet).
        target &= territory & (land if nt != "beach" else True)
        layers["nature_" + nt] = grid.distance(target)
        log(f"nature {nt}")

    harbour = grid.rasterize(feat["areas"].get("harbour", []) +
                             [g for g, _ in feat["points"].get("harbour", [])])
    layers["harbour"] = grid.distance(harbour & territory)
    log("harbours")

    markers = {}
    for st in config.STATION_TYPES:
        pts = feat["points"].get("station_" + st, [])
        target = grid.rasterize([g for g, _ in pts]) & territory
        layers["station_" + st] = grid.distance(target)
        markers["station_" + st] = _markers(grid, territory, pts)
        log(f"stations {st}")
    markers["harbour"] = _markers(
        grid, territory,
        [(g.centroid, nm) for g, nm in feat["points"].get("harbour", [])] +
        [(g.representative_point(), "") for g in feat["areas"].get("harbour", [])],
    )

    config.PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    save_grid(grid, land_dk, layers)
    with open(config.POINTS_PATH, "w", encoding="utf-8") as f:
        json.dump(markers, f, ensure_ascii=False)
    log(f"Saved {config.GRID_DIR} and {config.POINTS_PATH}")


def save_grid(grid: Grid, land_dk: np.ndarray, layers: dict[str, np.ndarray]):
    """Write uncompressed .npy files so the app can memory-map them."""
    if config.GRID_DIR.exists():
        shutil.rmtree(config.GRID_DIR)
    config.GRID_DIR.mkdir(parents=True)
    np.save(config.GRID_DIR / "land.npy", land_dk.astype(np.uint8))
    for name, arr in layers.items():
        np.save(config.GRID_DIR / f"d_{name}.npy", arr)
    meta = {"x0": grid.x0, "y1": grid.y1, "cell": grid.cell, "height": grid.height,
            "width": grid.width, "layers": sorted(layers), "built": time.time()}
    (config.GRID_DIR / "meta.json").write_text(json.dumps(meta, indent=1))


def _markers(grid: Grid, territory: np.ndarray, pts, dedupe_m=300.0):
    """Points inside Danish territory as [lat, lon, name], merging near-duplicates."""
    out = []
    kept = np.empty((0, 2))
    # Named points first so a duplicate keeps its name.
    for g, name in sorted(pts, key=lambda p: p[1] == ""):
        x, y = g.x, g.y
        r, c = grid.cell_of(x, y)
        if not (0 <= r < grid.height and 0 <= c < grid.width and territory[r, c]):
            continue
        scale = np.cos(np.radians(merc_y_to_lat(y)))
        if len(kept) and (np.hypot(*(kept - (x, y)).T).min() * scale) < dedupe_m:
            continue
        kept = np.vstack([kept, (x, y)])
        lon, lat = _TO_WGS.transform(x, y)
        out.append([round(lat, 6), round(lon, 6), name])
    return out


def main():
    build()


if __name__ == "__main__":
    main()
