"""Step 1: read the OSM extract and collect the relevant geometries.

Geometries are converted to Web Mercator (EPSG:3857) and pickled so the
slow OSM pass only has to run once.
"""

from __future__ import annotations

import pickle
import time
from collections import defaultdict

import numpy as np
import osmium
import shapely
from pyproj import Transformer

from . import config

_TO_MERC = Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)

AREA_KEYS = (
    "natural", "landuse", "leisure", "boundary", "harbour", "seamark:type",
    "industrial", "railway", "public_transport", "shop", "amenity", "healthcare",
    "social_facility",
)
NODE_KEYS = ("natural", "leisure", "harbour", "seamark:type", "industrial", "railway",
             "landuse", "shop", "amenity", "healthcare", "social_facility")

LAKE_WATER_VALUES = {None, "lake", "reservoir", "lagoon", "oxbow"}
HARBOUR_TAGS = (
    ("leisure", "marina"),
    ("harbour", "yes"),
    ("landuse", "harbour"),
    ("seamark:type", "harbour"),
    ("industrial", "port"),
)


def _to_merc(geom):
    return shapely.transform(geom, lambda xy: np.column_stack(_TO_MERC.transform(xy[:, 0], xy[:, 1])))


def _is_harbour(tags) -> bool:
    return any(tags.get(k) == v for k, v in HARBOUR_TAGS)


def _poi_type(tags) -> str | None:
    """Map grocery shops and institutions to their distance-grid layer names."""
    if tags.get("shop") in {"supermarket", "convenience", "grocery", "food", "greengrocer",
                            "deli"}:
        return "grocery"

    amenity = tags.get("amenity")
    if amenity == "school":
        return "institution_school"
    if amenity in ("kindergarten", "childcare") or tags.get("social_facility") == "day_care":
        return "institution_daycare"
    if amenity in ("college", "university"):
        return "institution_higher_education"
    if amenity in ("hospital", "clinic", "doctors", "dentist", "pharmacy") \
            or tags.get("healthcare") not in (None, "", "no"):
        return "institution_healthcare"
    if amenity == "library":
        return "institution_library"
    return None


def _station_kind(tags) -> str | None:
    """Raw station classification: 'metro', 'train', 'light' (S-tog or letbane,
    the Danish OSM tagging does not tell them apart), 'tram_stop' or None."""
    railway = tags.get("railway")
    if railway not in ("station", "halt", "tram_stop"):
        return None
    if tags.get("railway:preserved") == "yes" or tags.get("usage") == "tourism":
        return None
    if "historic" in tags or "tourism" in tags:
        return None
    station = tags.get("station")
    if station in ("miniature", "funicular", "monorail"):
        return None
    if station == "subway" or tags.get("subway") == "yes":
        return "metro"
    if railway == "tram_stop":
        return "tram_stop"
    if station in ("light_rail", "tram") or tags.get("light_rail") == "yes":
        return "light"
    return "train"


RAIL_ROUTES = ("train", "light_rail", "tram", "subway")


def _is_letbane_route(tags) -> bool:
    route = tags.get("route")
    if route == "tram":
        return True
    if route != "light_rail":
        return False
    return (tags.get("operator") == "Keolis" or tags.get("network") in ("Movia", "Midttrafik")
            or "letbane" in (tags.get("name") or "").lower())


def _route_member_nodes(pbf_path) -> dict[int, set]:
    """Node members of rail route relations -> {'letbane'} / {'other'} / both."""
    members: dict[int, set] = defaultdict(set)
    fp = osmium.FileProcessor(str(pbf_path), osmium.osm.RELATION) \
        .with_filter(osmium.filter.TagFilter(("type", "route")))
    for rel in fp:
        if rel.tags.get("route") not in RAIL_ROUTES:
            continue
        label = "letbane" if _is_letbane_route(rel.tags) else "other"
        for m in rel.members:
            if m.type == "n":
                members[m.ref].add(label)
    return members


def _classify_stations(candidates, member_xy, member_labels, max_dist=500.0):
    """Split raw station candidates into train / metro / light_rail point lists.

    S-tog stations and letbane stops share the same tags in Danish OSM, so a
    'light' station is classified by the route type of the nearest rail route
    stop. Bare tram_stop nodes are only kept when they belong to a letbane.
    """
    from scipy.spatial import cKDTree

    tree = cKDTree(member_xy) if len(member_xy) else None
    out = defaultdict(list)
    for geom, name, kind in candidates:
        if kind in ("metro", "train"):
            out["station_" + kind].append((geom, name))
            continue
        labels = set()
        if tree is not None:
            # Mercator distances are ~1.8x true distances in Denmark.
            d, i = tree.query((geom.x, geom.y), distance_upper_bound=max_dist * 1.8)
            if np.isfinite(d):
                labels = member_labels[i]
        if "letbane" in labels:
            out["station_light_rail"].append((geom, name))
        if kind == "light" and (labels != {"letbane"}):
            out["station_train"].append((geom, name))
    return out


def _nature_type(tags) -> str | None:
    natural = tags.get("natural")
    landuse = tags.get("landuse")
    if natural == "wood" or landuse == "forest":
        return "forest"
    if natural == "heath":
        return "heath"
    if natural == "beach":
        return "beach"
    if tags.get("leisure") == "nature_reserve" or tags.get("boundary") == "national_park":
        return "reserve"
    if tags.get("leisure") == "park":
        return "park"
    return None


def _is_lake(tags) -> bool:
    return tags.get("natural") == "water" and tags.get("water") in LAKE_WATER_VALUES \
        and tags.get("intermittent") != "yes"


def _is_denmark(tags) -> bool:
    return tags.get("boundary") == "administrative" and tags.get("admin_level") == "2" \
        and tags.get("ISO3166-1") == "DK"


def extract(pbf_path=config.PBF_PATH):
    t0 = time.time()
    wkb = osmium.geom.WKBFactory()
    areas: dict[str, list] = defaultdict(list)  # category -> list of WKB
    points: dict[str, list] = defaultdict(list)  # category -> list of (WKB, name)
    coast: list = []
    denmark: list = []
    stations: list = []  # (WKB, name, kind)

    print("Reading rail route relations...", flush=True)
    route_members = _route_member_nodes(pbf_path)
    member_xy: list = []
    member_labels: list = []

    fp = (
        osmium.FileProcessor(str(pbf_path))
        .with_locations()
        .with_areas(osmium.filter.KeyFilter(*AREA_KEYS))
        .with_filter(osmium.filter.KeyFilter(*set(AREA_KEYS + NODE_KEYS)))
    )
    n = 0
    for obj in fp:
        n += 1
        tags = obj.tags
        try:
            if obj.is_node():
                if obj.id in route_members:
                    member_xy.append(_TO_MERC.transform(obj.location.lon, obj.location.lat))
                    member_labels.append(route_members[obj.id])
                poi = _poi_type(tags)
                if poi:
                    points[poi].append((wkb.create_point(obj), tags.get("name", "")))
                kind = _station_kind(tags)
                if kind:
                    stations.append((wkb.create_point(obj), tags.get("name", ""), kind))
                if _is_harbour(tags):
                    points["harbour"].append((wkb.create_point(obj), tags.get("name", "")))
                if tags.get("natural") == "beach":
                    points["nature_beach"].append((wkb.create_point(obj), ""))
            elif obj.is_way():
                if tags.get("natural") == "coastline":
                    coast.append(wkb.create_linestring(obj))
            elif obj.is_area():
                if _is_denmark(tags):
                    denmark.append(wkb.create_multipolygon(obj))
                    continue
                if tags.get("natural") == "coastline":
                    continue
                geom = None
                nt = _nature_type(tags)
                if nt:
                    geom = wkb.create_multipolygon(obj)
                    areas["nature_" + nt].append(geom)
                if _is_lake(tags):
                    geom = geom or wkb.create_multipolygon(obj)
                    areas["lake"].append(geom)
                if _is_harbour(tags):
                    geom = geom or wkb.create_multipolygon(obj)
                    areas["harbour"].append(geom)
                poi = _poi_type(tags)
                if poi:
                    geom = geom or wkb.create_multipolygon(obj)
                    areas[poi].append(geom)
                kind = _station_kind(tags)
                if kind:
                    geom = geom or wkb.create_multipolygon(obj)
                    # Station areas are represented by their centroid.
                    c = shapely.from_wkb(geom).centroid
                    stations.append((c.wkb_hex, tags.get("name", ""), kind))
        except RuntimeError:
            # Broken geometry (e.g. missing node locations at extract border).
            continue
        if n % 500_000 == 0:
            print(f"  {n:,} objects scanned ({time.time() - t0:.0f}s)", flush=True)

    print(f"OSM pass done in {time.time() - t0:.0f}s; converting geometries...", flush=True)
    if not denmark:
        raise RuntimeError("Denmark boundary (ISO3166-1=DK, admin_level=2) not found in extract")

    def conv(wkbs):
        return list(_to_merc(shapely.from_wkb(np.array(wkbs, dtype=object))))

    station_geoms = conv([w for w, _, _ in stations])
    classified = _classify_stations(
        [(g, nm, k) for g, (_, nm, k) in zip(station_geoms, stations)],
        np.array(member_xy).reshape(-1, 2), member_labels,
    )
    result = {
        "areas": {k: conv(v) for k, v in areas.items()},
        "points": {
            **{k: list(zip(conv([w for w, _ in v]), [nm for _, nm in v]))
               for k, v in points.items()},
            **classified,
        },
        "coast": conv(coast),
        "denmark": shapely.union_all(conv(denmark)),
    }
    for k, v in result["areas"].items():
        print(f"  area  {k:20s} {len(v):>8,}")
    for k, v in result["points"].items():
        print(f"  point {k:20s} {len(v):>8,}")
    print(f"  coastline ways         {len(result['coast']):>8,}")
    return result


def main():
    config.PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    res = extract()
    with open(config.FEATURES_PATH, "wb") as f:
        pickle.dump(res, f, protocol=pickle.HIGHEST_PROTOCOL)
    print(f"Saved {config.FEATURES_PATH}")


if __name__ == "__main__":
    main()
