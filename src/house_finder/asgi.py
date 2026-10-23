"""ASGI entry point: the Streamlit GUI plus the map tile route."""

from pathlib import Path

import streamlit as st
from starlette.routing import Route

from house_finder.tiles import tile_endpoint

app = st.App(
    str(Path(__file__).with_name("app.py")),
    routes=[Route("/api/tiles/{z:int}/{x:int}/{y:int}.png", tile_endpoint)],
)
