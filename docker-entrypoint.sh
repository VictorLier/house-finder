#!/bin/sh
# Builds the map data into $HOUSE_FINDER_DATA on first start (or when
# HOUSE_FINDER_REBUILD=1), then starts the app. Extra arguments go to Streamlit.
set -eu

DATA="${HOUSE_FINDER_DATA:-/data}"

if ! touch "$DATA/.write-test" 2>/dev/null; then
    echo "ERROR: $DATA is not writable by uid $(id -u). Give that user write access" \
         "to the mounted folder (on TrueNAS: the 'apps' user, uid 568)." >&2
    exit 1
fi
rm -f "$DATA/.write-test"

if [ "${HOUSE_FINDER_REBUILD:-0}" = "1" ]; then
    echo "HOUSE_FINDER_REBUILD=1: rebuilding map data from the latest OpenStreetMap extract."
    house-finder-build --refresh --remove-sources
elif [ ! -f "$DATA/processed/grid/meta.json" ] || [ ! -f "$DATA/processed/points.json" ]; then
    echo "No map data in $DATA yet: downloading OpenStreetMap data and building the"
    echo "distance grids. This happens once and takes about 5-15 minutes."
    house-finder-build --remove-sources
fi

exec house-finder "$@"
