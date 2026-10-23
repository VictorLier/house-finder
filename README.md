# House Finder Denmark

Interactive map that highlights areas of Denmark close to **water** (coast and larger lakes),
**harbours/marinas**, **nature** (woods, heath, nature reserves/national parks, parks, beaches) and
**rail stations** (train incl. S-tog and local trains, Metro, letbane).

## Quick start (uv)

```powershell
uv sync
uv run house-finder-build   # one-time: downloads ~500 MB OSM data, builds grids (~4 min)
uv run house-finder         # opens the Streamlit GUI at http://localhost:8501
```

Re-run `uv run house-finder-build --refresh` to fetch the latest OpenStreetMap data.

## Hosting with Docker (e.g. TrueNAS SCALE)

Every push to `main` makes GitHub Actions build, smoke-test and publish the image
`ghcr.io/victorlier/house-finder:latest` (linux/amd64). There is nothing to build on the server.

* The map data is **not** in the image. On first start the container downloads the
  OpenStreetMap extract and builds the grids into the `/data` volume (~5-15 min, ~750 MB disk,
  a few GB RAM during the build). Later starts reuse it. Follow progress in the container logs.
* Set `HOUSE_FINDER_REBUILD=1` and restart to rebuild from fresh OSM data, then set it back to `0`.
* Runs as uid/gid 568 (TrueNAS `apps` user). The mounted folder must be writable by that user.
* Port 8501. Health check: `/_stcore/health`.

**TrueNAS SCALE (24.10 or newer)**

1. Create a dataset, e.g. `POOL/apps/house-finder`, and give the `apps` user modify permission.
2. **Apps → Discover Apps → ⋮ → Install via YAML**, paste
   [`deploy/truenas-compose.yaml`](deploy/truenas-compose.yaml) and change the dataset path.
3. Wait until the logs show the Streamlit URL, then open `http://<truenas-ip>:8501`.
4. To update after new commits: open the app in TrueNAS and choose **Update** / **Redeploy**
   (`pull_policy: always` fetches the newest image).

**Reverse proxy**

* Use its own (sub)domain, e.g. `houses.example.dk` → `http://<truenas-ip>:8501`. Serving it
  under a sub-path such as `/houses` is not supported (map tiles use absolute `/api/tiles/...` URLs).
* Enable **WebSocket** support (Nginx Proxy Manager: "Websockets Support"; Traefik and Caddy do
  this automatically). Without it the page stays blank.
* Put authentication in front of it (proxy auth, Authelia, etc.). The app has no login, and the
  listings from Boligsiden are for personal use only.

Plain Docker: `docker run -d -p 8501:8501 -v house-finder-data:/data ghcr.io/victorlier/house-finder:latest`

## Using the app

* Sidebar: enable/disable each criterion, choose its sub-types and set the maximum distance.
  Highlighted areas meet **all** enabled criteria.
* *Display* section: pick the highlight colour and opacity, and toggle "Dim non-matching land"
  (greys out land that does not match, so matching areas stand out). Matching areas have a dark outline.
* Click on the map to see the exact distances from that point.
* Use the layer button (top right of the map) to toggle station/harbour markers.

## How it works

| Step | File | What it does |
|------|------|--------------|
| Download | `pipeline.py` | Fetches `denmark-latest.osm.pbf` from Geofabrik |
| Extract | `extract.py` | Reads OSM with pyosmium and collects coastline, lakes, nature, harbours and stations |
| Grid | `build_grid.py` | Rasterizes to a ~90 m grid (Web Mercator), builds a land/sea mask from the coastline and computes straight-line distance grids |
| Grid storage | `data.py` | Grids saved as memory-mapped `.npy` files in `data/processed/grid/` (~700 MB) |
| Map tiles | `tiles.py`, `asgi.py` | Serves the highlight as map tiles at `/api/tiles/{z}/{x}/{y}.png` via `st.App`. Zoomed in, distances are interpolated per screen pixel (smooth, sharp edges); zoomed out, any matching cell is shown so small areas stay visible |
| GUI | `app.py` | Streamlit + Folium map, sidebar controls and point details (started through `asgi.py`) |

To run the app manually: `uv run streamlit run src/house_finder/asgi.py`.

For AI-assisted development, the official Streamlit agent skill (`developing-with-streamlit`)
is installed globally via `streamlit skills --global`.

Notes and definitions:

* Distances are straight-line ("as the crow flies"), accurate to about ±100 m.
* Only features inside Danish territory count (e.g. Swedish stations are ignored).
* Lakes: `natural=water` lakes/reservoirs; minimum size selectable (5, 20 or 100 ha).
* Harbours: marinas, `harbour=yes`, `landuse=harbour`, seamark harbours and ports.
* S-tog and letbane share the same tags in Danish OSM; stations are told apart using the
  rail route relations (letbane = Aarhus Letbane, Odense Letbane, Hovedstadens Letbane).

## Houses for sale

The sidebar section **Houses for sale** overlays current listings on the map – only those
inside the highlighted matching areas – as yellow markers (clustered when zoomed out).
Click a marker for photo, price, size, rooms, energy label and links to Boligsiden and the
realtor. A sortable table below the map lists the same houses with their distances.
Filter by property type, price, living area and minimum rooms.

Source: Boligsiden's **unofficial, undocumented** JSON API (`api.boligsiden.dk/search/cases`).
There is no official open API for Danish listings, and Boligsiden's `robots.txt` asks
automated clients not to use the API, so the app is deliberately restrained and meant for
**personal, non-commercial use only**:

* Listings are downloaded only when you press **Download / Refresh listings** in the
  sidebar – never automatically, and there is no command-line download.
* The result is stored in `data/processed/houses.json` (~30 MB) and refreshing is blocked
  for 24 hours after the last download. Only one download can run at a time.
* Requests are gzip-compressed, made one at a time with a 1 s pause (~100 requests,
  ~200 MB, ~6 minutes). The API caps paging at 10,000 results per query, so listings are
  fetched in automatically chosen price bands.

The API may change or block access at any time. Listings © Boligsiden and the realtors.

Map data © OpenStreetMap contributors (ODbL).
