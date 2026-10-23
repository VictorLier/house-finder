"""Render XYZ map tiles of the matching areas.

Zoomed out (screen pixel larger than a grid cell) a pixel is highlighted when
any matching cell falls inside it, so small areas stay visible. Zoomed in, the
distance fields are bilinearly interpolated per screen pixel, which gives smooth,
sharp boundaries instead of blurry upscaled grid squares.
"""

from __future__ import annotations

import functools
import io
import math
import re

import numpy as np
from PIL import Image
from scipy import ndimage

from .data import R_EARTH, Criteria, decode_criteria, get_data

TILE = 256
PAD = 2  # extra pixels on each side so outlines are continuous across tiles
OUTLINE_PX = 2
HALF_WORLD = math.pi * R_EARTH
DIM_RGBA = (25, 25, 25, 110)
_STYLE_RE = re.compile(r"^([0-9a-fA-F]{6})-(\d{1,3})-([01])$")


@functools.cache
def empty_tile() -> bytes:
    buf = io.BytesIO()
    Image.new("RGBA", (TILE, TILE), (0, 0, 0, 0)).save(buf, format="PNG")
    return buf.getvalue()


def parse_style(text: str):
    m = _STYLE_RE.match(text)
    if not m:
        raise ValueError("bad style")
    colour = tuple(int(m.group(1)[i:i + 2], 16) for i in (0, 2, 4))
    alpha = int(m.group(2))
    if alpha > 255:
        raise ValueError("bad alpha")
    return colour, alpha, m.group(3) == "1"


def encode_style(hex_colour: str, opacity: float, dim: bool) -> str:
    return f"{hex_colour.lstrip('#').lower()}-{round(opacity * 255)}-{int(dim)}"


def _block_sums(arr: np.ndarray, row_edges: np.ndarray, col_edges: np.ndarray) -> np.ndarray:
    """Sum of ``arr`` over the blocks between consecutive (clipped) edges."""
    s = np.zeros((arr.shape[0] + 1, arr.shape[1] + 1), dtype=np.int32)
    np.cumsum(np.cumsum(arr, axis=0, dtype=np.int32), axis=1, out=s[1:, 1:])
    r, c = row_edges[:, None], col_edges[None, :]
    return s[r[1:], c[:, 1:]] - s[r[:-1], c[:, 1:]] - s[r[1:], c[:, :-1]] + s[r[:-1], c[:, :-1]]


def _pooled(data, criteria, rows_e, cols_e):
    """Zoomed out: aggregate whole grid cells into each screen pixel."""
    r_lo, r_hi = int(np.clip(rows_e[0], 0, data.height)), int(np.clip(rows_e[-1], 0, data.height))
    c_lo, c_hi = int(np.clip(cols_e[0], 0, data.width)), int(np.clip(cols_e[-1], 0, data.width))
    if r_lo >= r_hi or c_lo >= c_hi:
        return None
    rr = np.clip(np.floor(rows_e).astype(int), r_lo, r_hi) - r_lo
    cc = np.clip(np.floor(cols_e).astype(int), c_lo, c_hi) - c_lo
    win = (slice(r_lo, r_hi), slice(c_lo, c_hi))
    match = _block_sums(data.match(criteria)[win], rr, cc) > 0
    land_count = _block_sums(np.asarray(data.land[win]), rr, cc)
    cells = np.diff(rr)[:, None] * np.diff(cc)[None, :]
    land = land_count * 2 >= np.maximum(cells, 1)
    land &= cells > 0
    return match, land


def _interpolated(data, criteria, row_f, col_f):
    """Zoomed in: bilinear interpolation of the land mask and distance fields."""
    r_lo = max(0, int(np.floor(row_f.min())))
    r_hi = min(data.height, int(np.floor(row_f.max())) + 2)
    c_lo = max(0, int(np.floor(col_f.min())))
    c_hi = min(data.width, int(np.floor(col_f.max())) + 2)
    if r_lo >= r_hi or c_lo >= c_hi:
        return None
    rows, cols = slice(r_lo, r_hi), slice(c_lo, c_hi)
    coords = np.array(np.meshgrid(row_f - r_lo, col_f - c_lo, indexing="ij"))

    def interp(arr):
        return ndimage.map_coordinates(arr.astype(np.float32), coords, order=1, mode="nearest")

    inside = ((row_f[:, None] >= -0.5) & (row_f[:, None] < data.height - 0.5)
              & (col_f[None, :] >= -0.5) & (col_f[None, :] < data.width - 0.5))
    # Smooth the binary land mask (with a margin) so coastlines are round, not stepped.
    m = 3
    lr0, lr1 = max(0, r_lo - m), min(data.height, r_hi + m)
    lc0, lc1 = max(0, c_lo - m), min(data.width, c_hi + m)
    land_win = ndimage.gaussian_filter(np.asarray(data.land[lr0:lr1, lc0:lc1], np.float32),
                                       sigma=0.8, mode="nearest")
    land_win = land_win[r_lo - lr0:r_lo - lr0 + (r_hi - r_lo), c_lo - lc0:c_lo - lc0 + (c_hi - c_lo)]
    land = (interp(land_win) >= 0.5) & inside
    match = land.copy()
    for layers, max_m in criteria:
        match &= interp(data.criterion_min(layers, rows, cols)) <= max_m
    return match, land


@functools.lru_cache(maxsize=4096)
def render_tile(z: int, x: int, y: int, criteria: Criteria, style: str) -> bytes:
    colour, alpha, dim = parse_style(style)
    data = get_data()
    size = 2 * HALF_WORLD / 2 ** z
    px = size / TILE
    minx, maxy = -HALF_WORLD + x * size, HALF_WORLD - y * size
    gx0, gy0, gx1, gy1 = data.merc_bounds
    if minx > gx1 or minx + size < gx0 or maxy < gy0 or maxy - size > gy1:
        return empty_tile()

    edges = np.arange(-PAD, TILE + PAD + 1)
    cols_e = (minx + edges * px - data.x0) / data.cell  # pixel edges in grid units
    rows_e = (data.y1 - (maxy - edges * px)) / data.cell
    if px >= data.cell:
        res = _pooled(data, criteria, rows_e, cols_e)
    else:
        centres = edges[:-1] + 0.5
        col_f = (minx + centres * px - data.x0) / data.cell - 0.5  # cell centres at integers
        row_f = (data.y1 - (maxy - centres * px)) / data.cell - 0.5
        res = _interpolated(data, criteria, row_f, col_f)
    if res is None:
        return empty_tile()
    match, land = res
    if not (match.any() or (dim and land.any())):
        return empty_tile()

    rgba = np.zeros(match.shape + (4,), dtype=np.uint8)
    if dim:
        rgba[land & ~match] = DIM_RGBA
    rgba[match] = (*colour, alpha)
    inner = ndimage.binary_erosion(match, structure=np.ones((3, 3), bool),
                                   iterations=OUTLINE_PX, border_value=1)
    outline = tuple(int(c * 0.45) for c in colour) + (255,)
    rgba[match & ~inner] = outline

    buf = io.BytesIO()
    Image.fromarray(rgba[PAD:-PAD, PAD:-PAD], mode="RGBA").save(buf, format="PNG",
                                                               compress_level=3)
    return buf.getvalue()


def tile_url(criteria: Criteria, style: str) -> str:
    from urllib.parse import quote

    from .data import encode_criteria

    return (f"/api/tiles/{{z}}/{{x}}/{{y}}.png?c={quote(encode_criteria(criteria))}"
            f"&s={style}&v={get_data().version}")


async def tile_endpoint(request):
    """Starlette route: /api/tiles/{z}/{x}/{y}.png?c=<criteria>&s=<style>."""
    from starlette.concurrency import run_in_threadpool
    from starlette.responses import PlainTextResponse, Response

    try:
        z, x, y = (int(request.path_params[k]) for k in ("z", "x", "y"))
        if not (0 <= z <= 22 and 0 <= x < 2 ** z and 0 <= y < 2 ** z):
            raise ValueError("tile out of range")
        data = get_data()
        criteria = decode_criteria(request.query_params.get("c", ""), data.layers)
        style = request.query_params.get("s", "")
        parse_style(style)
    except (ValueError, KeyError) as exc:
        return PlainTextResponse(str(exc), status_code=400)
    png = await run_in_threadpool(render_tile, z, x, y, criteria, style)
    return Response(png, media_type="image/png",
                    headers={"Cache-Control": "public, max-age=86400"})
