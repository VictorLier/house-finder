"""House Finder Denmark."""

import sys
from pathlib import Path


def main() -> None:
    """Launch the Streamlit GUI."""
    from streamlit.web import cli

    sys.argv = ["streamlit", "run", str(Path(__file__).with_name("asgi.py")), *sys.argv[1:]]
    sys.exit(cli.main())
