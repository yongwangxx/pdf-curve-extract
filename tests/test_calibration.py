"""Tick calibration regressions for padded frames, reversals, and masked tips."""
import importlib.util
import json
import math
from pathlib import Path
import sys
import tempfile
import unittest

import fitz

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
SPEC = importlib.util.spec_from_file_location("extract_curves", ROOT / "extract_curves.py")
curves = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(curves)


def configuration(**overrides):
    config = {"page": 1, "panel": [20, 20, 180, 180],
              "x_axis": {"scale": "linear", "limits": [0, 1000]},
              "y_axis": {"scale": "linear", "limits": [0, 10]},
              "series": [{"name": "signal", "color": [0, 0, 1]}]}
    config.update(overrides)
    return curves.validate_config(config)


class CalibrationTests(unittest.TestCase):
    def test_interior_ticks_do_not_masquerade_as_frame_edges(self):
        config = configuration(
            x_axis={"scale": "linear", "anchors": [[40, 0], [140, 100]], "check_ticks": [[90, 50]]},
            y_axis={"scale": "linear", "anchors": [[160, 0], [60, 10]], "check_ticks": [[110, 5]]})
        self.assertEqual(curves.data_point((40, 160), config), (0, 0))
        self.assertEqual(curves.data_point((140, 60), config), (100, 10))
        self.assertEqual(curves.axis_limits(config, "x"), [-20, 140])
        self.assertEqual(curves.axis_limits(config, "y"), [-2, 14])
        diagnostics = curves.calibration_diagnostics(config)
        for axis in diagnostics.values():
            self.assertEqual(axis["max_abs_coordinate_residual"], 0)
            self.assertEqual(axis["max_abs_value_residual"], 0)
            self.assertEqual(axis["assessment"], "not_assigned")

    def test_log_anchors_on_both_axes_and_unsorted_coordinate_order(self):
        config = configuration(
            x_axis={"scale": "log", "anchors": [[140, 100], [40, 1]], "check_ticks": [[90, 10]]},
            y_axis={"scale": "log", "anchors": [[60, 100], [160, 1]], "check_ticks": [[110, 10]]})
        self.assertEqual(curves.data_point((40, 160), config), (1, 1))
        self.assertEqual(curves.data_point((140, 60), config), (100, 100))
        for value in curves.data_point((90, 110), config):
            self.assertAlmostEqual(value, 10)
        for axis in curves.calibration_diagnostics(config).values():
            self.assertAlmostEqual(axis["max_abs_coordinate_residual"], 0)

    def test_reversed_axes_and_observed_tick_error(self):
        config = configuration(
            x_axis={"scale": "linear", "anchors": [[40, 100], [140, 0]], "check_ticks": [[91, 50]]},
            y_axis={"scale": "log", "anchors": [[60, 1], [160, 100]], "check_ticks": [[111, 10]]})
        self.assertEqual(curves.data_point((40, 160), config), (100, 100))
        diagnostics = curves.calibration_diagnostics(config)
        x_tick = diagnostics["x_axis"]["check_ticks"][0]
        self.assertAlmostEqual(x_tick["coordinate_residual"], 1)
        self.assertAlmostEqual(x_tick["value_residual"], -1)
        y_tick = diagnostics["y_axis"]["check_ticks"][0]
        self.assertAlmostEqual(y_tick["coordinate_residual"], 1)
        self.assertAlmostEqual(y_tick["transformed_value_residual"], math.log(100) / 100)

    def test_limits_still_support_independent_check_ticks(self):
        config = configuration(x_axis={"scale": "linear", "limits": [0, 1000],
                                       "check_ticks": [[100, 510]]})
        check = curves.calibration_diagnostics(config)["x_axis"]["check_ticks"][0]
        self.assertEqual(check["predicted_value"], 500)
        self.assertEqual(check["value_residual"], -10)
        self.assertAlmostEqual(check["coordinate_residual"], -1.6)

    def test_invalid_calibrations_fail_predictably(self):
        invalid = [
            {"scale": "linear"},
            {"scale": "linear", "limits": [0, 1], "anchors": [[20, 0], [180, 1]]},
            {"scale": "linear", "anchors": [[20, 0]]},
            {"scale": "linear", "anchors": [[20, 0], [20, 1]]},
            {"scale": "linear", "anchors": [[20, 1], [180, 1]]},
            {"scale": "linear", "anchors": [[20, 0], [math.inf, 1]]},
            {"scale": "linear", "anchors": [[20, 0], [180, math.nan]]},
            {"scale": "linear", "anchors": [[20, 0], [180, True]]},
            {"scale": "linear", "anchors": [[20, 0], [180, 10**1000]]},
            {"scale": "linear", "anchors": [[20, 0], [180, 1]], "check_ticks": [[90]]},
            {"scale": "log", "anchors": [[20, 0], [180, 1]]},
            {"scale": "log", "anchors": [[20, 1], [180, 10]], "check_ticks": [[90, -1]]},
            {"scale": "log", "anchors": [[40, 1e-300], [40.1, 1e300]]},
        ]
        for axis in invalid:
            with self.subTest(axis=axis), self.assertRaises(curves.ExtractionError):
                configuration(x_axis=axis)

    def test_manifest_contains_calibration_and_native_comparison_output(self):
        config = configuration(
            x_axis={"scale": "linear", "anchors": [[40, 0], [140, 100]], "check_ticks": [[90, 50]],
                    "private_note": "excluded_field"})
        doc = fitz.open()
        page = doc.new_page(width=200, height=200)
        page.draw_line((40, 160), (140, 60), color=(0, 0, 1))
        with doc, tempfile.TemporaryDirectory() as tmp:
            diagnostics = {}
            extracted, warnings = curves.extract(page, config, diagnostics)
            manifest = curves.write_outputs(page, config, extracted, doc.tobytes(),
                                            Path(tmp) / "case", warnings, diagnostics)
            self.assertEqual(manifest["series"]["signal"]["x_range"], [0, 100])
            self.assertEqual(manifest["calibration_diagnostics"]["x_axis"]["method"], "tick_anchors")
            self.assertIn("comparison", manifest["outputs"])
            self.assertTrue((Path(tmp) / "case_comparison.png").exists())
            self.assertNotIn(tmp, json.dumps(manifest))
            self.assertNotIn("excluded_field", json.dumps(manifest))

    def test_masks_report_removed_tips_without_bridging(self):
        config = configuration(exclude_rects=[[150, 90, 190, 110]])
        doc = fitz.open()
        page = doc.new_page(width=200, height=200)
        page.draw_line((20, 100), (180, 100), color=(0, 0, 1))
        diagnostics = {}
        with doc:
            extracted, warnings = curves.extract(page, config, diagnostics)
        self.assertEqual(extracted["signal"][0]["points"][-1], (150, 100))
        stats = diagnostics["exclusions"]["series"]["signal"]
        self.assertEqual(stats["affected_path_count"], 1)
        self.assertEqual(stats["affected_input_segments"], 1)
        self.assertAlmostEqual(stats["removed_length_source_units"], 30)
        self.assertTrue(any("curve tips" in warning for warning in warnings))

    def test_overlapping_masks_do_not_double_count_removed_length(self):
        config = configuration(exclude_rects=[[90, 90, 130, 110], [110, 90, 150, 110]])
        doc = fitz.open()
        page = doc.new_page(width=200, height=200)
        page.draw_line((20, 100), (180, 100), color=(0, 0, 1))
        diagnostics = {}
        with doc:
            extracted, _ = curves.extract(page, config, diagnostics)
        self.assertEqual(len(extracted["signal"]), 2)
        self.assertAlmostEqual(diagnostics["exclusions"]["series"]["signal"]["removed_length_source_units"], 60)

    def test_fully_erased_series_explains_mask_effect(self):
        config = configuration(exclude_rects=[[10, 90, 190, 110]])
        doc = fitz.open()
        page = doc.new_page(width=200, height=200)
        page.draw_line((20, 100), (180, 100), color=(0, 0, 1))
        with doc, self.assertRaisesRegex(curves.ExtractionError, "exclusions removed selected geometry"):
            curves.extract(page, config)

    def test_inventory_exposes_fill_only_markers_and_clip_state(self):
        doc = fitz.open()
        page = doc.new_page(width=200, height=200)
        page.draw_rect((40, 40, 50, 50), color=None, fill=(1, 0, 0))
        with doc:
            rows = curves.inventory(page)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["type"], "f")
        self.assertIsNone(rows[0]["color"])
        self.assertEqual(rows[0]["fill"], (1, 0, 0))
        self.assertEqual(rows[0]["native_clip_status"], "none")

    def test_inventory_marks_nonrectangular_native_clips(self):
        doc = fitz.open()
        page = doc.new_page(width=200, height=200)
        page.draw_line((0, 0), (1, 1))
        doc.update_stream(page.get_contents()[0],
                          b"q 40 40 m 160 40 l 100 160 l h W n 1 0 0 rg 95 95 10 10 re f Q")
        with doc:
            rows = curves.inventory(page)
        self.assertEqual(rows[0]["path_index"], 1)
        self.assertEqual(rows[0]["native_clip_status"], "nonrectangular")
        self.assertEqual(rows[0]["native_clips"], [None])

    def test_source_clip_preserves_labels_overlay_origin_and_data(self):
        from PIL import Image

        config = configuration(source_clip=[5.25, 5.75, 195.25, 195.75], dpi=144)
        doc = fitz.open()
        page = doc.new_page(width=200, height=200)
        page.draw_line((20, 100), (180, 100), color=(0, 0, 1))
        page.insert_text((25, 190), "Axis label outside panel", fontsize=6, color=(0, 0, 0))
        with doc, tempfile.TemporaryDirectory() as tmp:
            baseline, _ = curves.extract(page, configuration())
            extracted, warnings = curves.extract(page, config)
            self.assertEqual(extracted, baseline)
            self.assertEqual(curves.data_point((100, 100), config), (500, 5))
            pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), clip=fitz.Rect(config["source_clip"]))
            manifest = curves.write_outputs(page, config, extracted, doc.tobytes(),
                                            Path(tmp) / "case", warnings)
            self.assertEqual(manifest["rendering"]["source_clip"], config["source_clip"])
            with Image.open(Path(tmp) / "case_validation.png") as overlay:
                self.assertEqual(overlay.size, (pix.width, pix.height))
                self.assertEqual(overlay.getpixel((200 - pix.x, 200 - pix.y)), (255, 0, 200))
            with Image.open(Path(tmp) / "case_panel.png") as source:
                label_region = source.crop((50-pix.x, 364-pix.y, 190-pix.x, 382-pix.y))
                self.assertLess(min(label_region.convert("L").getdata()), 100)
            outside = configuration(source_clip=[0, 0, 201, 200])
            with self.assertRaisesRegex(curves.ExtractionError, "source_clip lies outside"):
                curves.extract(page, outside)
        with self.assertRaisesRegex(curves.ExtractionError, "source_clip must contain"):
            configuration(source_clip=[30, 5, 195, 195])


if __name__ == "__main__":
    unittest.main()
