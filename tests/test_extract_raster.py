"""Tests use known native pixels, not expected values reconstructed from the implementation."""
import csv
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image

import extract_raster as raster
from extract_curves import ExtractionError


class RasterTests(unittest.TestCase):
    def config(self, **updates):
        raw = {"panel": [1, 1, 9, 9], "x_axis": {"scale": "linear", "limits": [0, 8]},
               "y_axis": {"scale": "linear", "limits": [0, 8]},
               "series": [{"name": "blue", "color": [0, 0, 1]}], "color_tolerance": 0}
        raw.update(updates)
        return raster.validate_config(raw)

    def image(self):
        rgb = np.full((11, 11, 3), 255, dtype=np.uint8)
        rgb[3, 5] = [0, 0, 255]
        rgb[7, 8] = [0, 0, 255]
        return Image.fromarray(rgb)

    def test_known_pixels_and_log_calibration(self):
        config = self.config(x_axis={"scale": "log", "limits": [1, 100]},
                             y_axis={"scale": "log", "limits": [1, 100]})
        result = raster.extract(self.image(), config)
        self.assertEqual(result["series"]["blue"]["retained_pixel_count"], 2)
        self.assertTrue(result["masks"][0][3, 5])
        self.assertAlmostEqual(raster.data_point((5, 3), config)[0], 10)
        self.assertAlmostEqual(raster.data_point((5, 3), config)[1], 10**1.5)

    def test_tick_anchors_and_reversed_axis(self):
        config = self.config(x_axis={"scale": "log", "anchors": [[1, 100], [9, 1]],
                                     "check_ticks": [[5, 10]]},
                             y_axis={"scale": "linear", "anchors": [[1, 0], [9, 8]]})
        self.assertAlmostEqual(raster.data_point((5, 3), config)[0], 10)
        self.assertAlmostEqual(raster.data_point((5, 3), config)[1], 2)
        from extract_curves import calibration_diagnostics
        self.assertAlmostEqual(calibration_diagnostics(config)["x_axis"]["max_abs_coordinate_residual"], 0)

    def test_same_color_is_ambiguous_never_winner(self):
        config = self.config(series=[{"name": "one", "color": [0, 0, 1]},
                                     {"name": "two", "color": [0, 0, 1]}])
        result = raster.extract(self.image(), config)
        self.assertEqual(int(result["ambiguous"].sum()), 2)
        self.assertFalse(any(mask.any() for mask in result["masks"]))
        self.assertEqual(result["series"]["one"]["ambiguous_unexcluded_pixel_count"], 2)

    def test_exclusion_audit_counts_removed_stroke(self):
        config = self.config(exclude_rects=[[4, 2, 5, 3]])
        result = raster.extract(self.image(), config)
        self.assertTrue(result["excluded_masks"][0][3, 5])
        self.assertFalse(result["masks"][0][3, 5])
        self.assertEqual(result["series"]["blue"]["excluded_candidate_pixel_count"], 1)
        self.assertEqual(result["series"]["blue"]["retained_pixel_count"], 1)

    def test_source_clip_does_not_shift_native_coordinates(self):
        config = self.config(source_clip=[0, 0, 11, 11])
        result = raster.extract(self.image(), config)
        self.assertEqual(result["source_clip"], [0, 0, 11, 11])
        self.assertTrue(result["masks"][0][3, 5])
        self.assertEqual(raster.data_point((5, 3), config), (4, 6))
        config["source_clip"] = [2, 2, 10, 10]
        with self.assertRaises(ExtractionError):
            raster.extract(self.image(), config)

    def test_white_mixture_is_explicit(self):
        rgb = np.asarray(self.image()).copy()
        rgb[4, 4] = [128, 128, 255]
        image = Image.fromarray(rgb)
        self.assertEqual(raster.extract(image, self.config())["series"]["blue"]["retained_pixel_count"], 2)
        result = raster.extract(image, self.config(white_mixture_min=.4))
        self.assertEqual(result["series"]["blue"]["retained_pixel_count"], 3)

    def test_white_background_admission_warns_without_silent_removal(self):
        result = raster.extract(self.image(), self.config(color_tolerance=.1, white_mixture_min=.1))
        self.assertTrue(any("admits pure white" in message for message in result["warnings"]))
        self.assertTrue(result["masks"][0][1, 1])
        self.assertEqual(result["series"]["blue"]["retained_pixel_count"], 81)

    def test_panel_clips_by_inclusive_centers(self):
        config = self.config(panel=[5, 3, 9, 9])
        self.assertEqual(raster.extract(self.image(), config)["series"]["blue"]["retained_pixel_count"], 2)
        config["panel"] = [5.1, 3.1, 9, 9]
        self.assertEqual(raster.extract(self.image(), config)["series"]["blue"]["retained_pixel_count"], 1)

    def test_invalid_config_and_transparency(self):
        for kwargs in ({"color_tolerance": -1}, {"white_mixture_min": 0}, {"source_clip": [0, 0, 2.5, 5]},
                       {"series": [{"name": "bad/name", "color": [0, 0, 1]}]}, {"exclude_rects": None}):
            with self.assertRaises(ExtractionError):
                self.config(**kwargs)
        stream = io.BytesIO()
        Image.new("RGBA", (10, 10), (0, 0, 255, 100)).save(stream, format="PNG")
        with self.assertRaises(ExtractionError):
            raster.load_image(stream.getvalue())

    def test_outputs_atomic_no_overwrite_and_safe_manifest(self):
        config = self.config(exclude_rects=[[4, 2, 5, 3]], source_clip=[0, 0, 11, 11])
        config["arbitrary_private_notes"] = "must never be serialized"
        image = self.image()
        stream = io.BytesIO()
        image.save(stream, format="PNG")
        result = raster.extract(image, config)
        with tempfile.TemporaryDirectory() as folder:
            prefix = Path(folder)/"case"
            with patch.object(raster, "render_extracted", side_effect=RuntimeError("render failed")):
                with self.assertRaises(RuntimeError):
                    raster.write_outputs(image, config, result, stream.getvalue(), prefix)
            self.assertEqual(list(Path(folder).iterdir()), [])
            manifest = raster.write_outputs(image, config, result, stream.getvalue(), prefix)
            with (Path(folder)/"case_blue_visible_pixels.csv").open() as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(len(rows), 1)
            self.assertEqual((rows[0]["native_x_px"], rows[0]["native_y_px"]), ("8", "7"))
            self.assertEqual((float(rows[0]["x"]), float(rows[0]["y"])), (7, 2))
            encoded = json.dumps(manifest)
            self.assertNotIn(folder, encoded)
            self.assertNotIn("must never", encoded)
            self.assertFalse(manifest["fitting_applied"])
            with Image.open(Path(folder)/"case_blue_mask.png") as mask_image:
                self.assertEqual(mask_image.size, (11, 11))
            with self.assertRaises(ExtractionError):
                raster.write_outputs(image, config, result, stream.getvalue(), prefix)


if __name__ == "__main__":
    unittest.main()
