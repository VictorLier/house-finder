import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from house_finder import config
from house_finder.data import data_ready


class DataReadyTests(unittest.TestCase):
    def test_old_grid_without_new_layers_is_not_ready(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            grid_dir = root / "grid"
            grid_dir.mkdir()
            (grid_dir / "meta.json").write_text(json.dumps({"layers": ["coast"]}))
            points_path = root / "points.json"
            points_path.write_text("{}")

            with patch.object(config, "GRID_DIR", grid_dir), \
                    patch.object(config, "POINTS_PATH", points_path):
                self.assertFalse(data_ready())

    def test_complete_grid_is_ready(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            grid_dir = root / "grid"
            grid_dir.mkdir()
            layers = {
                "coast", "harbour", "grocery",
                *(f"lake_{ha}" for ha in config.LAKE_MIN_AREAS_HA),
                *(f"nature_{kind}" for kind in config.NATURE_TYPES),
                *(f"station_{kind}" for kind in config.STATION_TYPES),
                *(f"institution_{kind}" for kind in config.INSTITUTION_TYPES),
            }
            (grid_dir / "meta.json").write_text(json.dumps({"layers": sorted(layers)}))
            points_path = root / "points.json"
            points_path.write_text("{}")

            with patch.object(config, "GRID_DIR", grid_dir), \
                    patch.object(config, "POINTS_PATH", points_path):
                self.assertTrue(data_ready())


if __name__ == "__main__":
    unittest.main()
