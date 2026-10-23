"""Houses for sale from Boligsiden's (unofficial, undocumented) search API.

Boligsiden's robots.txt asks automated clients not to crawl the API, so this module
is deliberately polite: it only runs when the user presses the button in the GUI,
keeps the result on disk for ``HOUSES_MAX_AGE_HOURS`` and waits between requests.
For personal, non-commercial use only. Listings remain © Boligsiden and the realtors.
"""

from __future__ import annotations

import gzip
import http.client
import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

from . import config

PER_PAGE = 500
MAX_DEPTH = 10_000  # API refuses page * per_page beyond this
MAX_PRICE = 1_000_000_000
USER_AGENT = "house-finder/0.1 (personal house search; low volume)"
_download_lock = threading.Lock()

PROPERTY_TYPES = {
    "villa": "House (villa)",
    "terraced house": "Terraced house",
    "villa apartment": "Villa apartment",
    "condo": "Apartment (ejerlejlighed)",
    "cooperative": "Cooperative (andel)",
    "holiday house": "Holiday home",
    "farm": "Farm",
    "hobby farm": "Hobby farm",
    "full year plot": "Plot (year-round)",
    "holiday plot": "Plot (holiday)",
    "houseboat": "Houseboat",
    "plant farm": "Farm (crops)",
    "pig farm": "Farm (pigs)",
    "cattle farm": "Farm (cattle)",
    "forest": "Forest property",
    "special": "Special property",
}


class HouseFetchError(RuntimeError):
    pass


def _get(params: dict, last_call: list[float]) -> dict:
    wait = config.HOUSES_REQUEST_DELAY_S - (time.monotonic() - last_call[0])
    if wait > 0:
        time.sleep(wait)
    url = f"{config.HOUSES_API}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT,
                                               "Accept": "application/json",
                                               "Accept-Encoding": "gzip"})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                body = r.read()
                if r.headers.get("Content-Encoding") == "gzip":
                    body = gzip.decompress(body)
            last_call[0] = time.monotonic()
            return json.loads(body)
        except urllib.error.HTTPError as e:
            last_call[0] = time.monotonic()
            if e.code < 500 and e.code != 429:
                raise HouseFetchError(f"Boligsiden API returned {e.code} for {url}") from e
            err = e
        except (urllib.error.URLError, TimeoutError, http.client.IncompleteRead,
                EOFError, OSError, ValueError) as e:  # ValueError covers bad JSON/gzip
            last_call[0] = time.monotonic()
            err = e
        if attempt == 2:
            raise HouseFetchError(f"Could not download from Boligsiden API: {err}") from err
        time.sleep(5 * (attempt + 1))
    raise AssertionError("unreachable")


def _price_ranges(last_call, progress) -> tuple[list[tuple[int, int, int]], int]:
    """Split [0, MAX_PRICE] into price ranges each small enough to page through."""
    total = _get({"per_page": 1, "page": 1}, last_call)["totalHits"]
    out, todo = [], [(0, MAX_PRICE)]
    while todo:
        lo, hi = todo.pop()
        n = _get({"per_page": 1, "page": 1, "priceMin": lo, "priceMax": hi},
                 last_call)["totalHits"]
        progress(f"Planning: {lo:,}–{hi:,} kr has {n:,} listings")
        if n <= MAX_DEPTH or hi - lo < 1000:
            if n:
                out.append((lo, hi, n))
        else:
            mid = (lo + hi) // 2
            todo += [(mid + 1, hi), (lo, mid)]
    return out, total


def _record(c: dict) -> dict | None:
    coords = c.get("coordinates") or {}
    lat, lon = coords.get("lat"), coords.get("lon")
    if lat is None or lon is None:
        return None
    a = c.get("address") or {}
    street = " ".join(str(p) for p in (a.get("roadName"), a.get("houseNumber")) if p)
    unit = ", ".join(str(p) for p in (a.get("floor"), a.get("door")) if p)
    town = " ".join(str(p) for p in (a.get("zipCode"), a.get("cityName")) if p)
    address = ", ".join(p for p in (street + (f", {unit}" if unit else ""), town) if p)
    image = None
    for src in (c.get("defaultImage") or {}).get("imageSources", []):
        if src.get("size", {}).get("width") == 300:
            image = src.get("url")
    slug = a.get("slug") or c.get("slug")
    return {
        "id": c.get("caseID"),
        "lat": lat,
        "lon": lon,
        "address": address or "Unknown address",
        "type": c.get("addressType") or a.get("addressType"),
        "price": c.get("priceCash"),
        "living_m2": c.get("housingArea"),
        "lot_m2": c.get("lotArea"),
        "rooms": c.get("numberOfRooms"),
        "year_built": c.get("yearBuilt"),
        "energy": (c.get("energyLabel") or "").upper() or None,
        "monthly": c.get("monthlyExpense"),
        "days": c.get("daysOnMarket"),
        "image": image,
        "boligsiden_url": f"https://www.boligsiden.dk/adresse/{slug}" if slug else None,
        "realtor_url": c.get("caseUrl"),
    }


def fetch_houses(progress=lambda msg, frac=None: None) -> dict:
    """Download all current listings (~100 requests) and save them to HOUSES_PATH.

    Only called from the GUI button. Refuses to run if the saved listings are younger
    than ``HOUSES_MAX_AGE_HOURS`` or another download is already in progress.
    """
    if not _download_lock.acquire(blocking=False):
        raise HouseFetchError("A download is already in progress.")
    try:
        if is_fresh(load_houses()):
            raise HouseFetchError(
                f"Listings were downloaded less than {config.HOUSES_MAX_AGE_HOURS} hours ago.")
        return _fetch_all(progress)
    finally:
        _download_lock.release()


def _fetch_all(progress) -> dict:
    last_call = [0.0]
    ranges, total = _price_ranges(last_call, progress)
    pages = sum(-(-n // PER_PAGE) for *_, n in ranges)
    houses, done = {}, 0
    for lo, hi, n in ranges:
        for page in range(1, -(-n // PER_PAGE) + 1):
            d = _get({"per_page": PER_PAGE, "page": page, "priceMin": lo, "priceMax": hi},
                     last_call)
            for c in d.get("cases", []):
                rec = _record(c)
                if rec and rec["id"]:
                    houses[rec["id"]] = rec
            done += 1
            progress(f"Downloaded page {done}/{pages} ({len(houses):,} listings)", done / pages)
    result = {
        "fetched": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": config.HOUSES_API,
        "total_hits": total,
        "houses": list(houses.values()),
    }
    config.HOUSES_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = config.HOUSES_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    tmp.replace(config.HOUSES_PATH)
    return result


def load_houses() -> dict | None:
    if not config.HOUSES_PATH.exists():
        return None
    return json.loads(config.HOUSES_PATH.read_text(encoding="utf-8"))


def age(result: dict) -> timedelta:
    return datetime.now(timezone.utc) - datetime.fromisoformat(result["fetched"])


def is_fresh(result: dict | None) -> bool:
    return result is not None and age(result) < timedelta(hours=config.HOUSES_MAX_AGE_HOURS)
