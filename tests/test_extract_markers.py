"""Known-geometry marker tests with explicit selection and failure cases."""
import csv
import hashlib
import json
import math
from pathlib import Path
import sys
import tempfile
import unittest

import fitz

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import extract_markers as markers


def configuration(**overrides):
    config = {"page": 1, "panel": [20, 20, 180, 180],
              "x_axis": {"scale": "log", "limits": [1, 100]},
              "y_axis": {"scale": "log", "limits": [0.01, 100]},
              "series": [{"name": "signal", "color": [0, 0, 1],
                          "marker_shape": "square", "marker_path_indices": [0]}]}
    config.update(overrides)
    return markers.validate_config(config)


def document(shape="square", center=(100, 100), fill_only=False):
    doc = fitz.open()
    page = doc.new_page(width=200, height=200)
    pen = page.new_shape()
    x, y = center
    if shape == "square":
        pen.draw_rect((x-3, y-3, x+3, y+3))
    elif shape in ("circle", "ring"):
        pen.draw_circle(center, 3)
        if shape == "ring":
            pen.draw_circle(center, 2)
    elif shape == "cross":
        pen.draw_line((x-3, y-3), (x+3, y+3))
        pen.draw_line((x-3, y+3), (x+3, y-3))
    pen.finish(color=None if fill_only else (0, 0, 1), fill=(0, 0, 1) if fill_only else None,
               even_odd=shape == "ring", closePath=False)
    pen.commit()
    return doc, page


class MarkerTests(unittest.TestCase):
    def test_logarithmic_center_is_transformed_after_recovery(self):
        doc, page = document()
        with doc:
            row = markers.extract(page, configuration())["signal"][0]
        self.assertAlmostEqual(row["x"], 10)
        self.assertAlmostEqual(row["y"], 1)
        wrong_edge_mean = (100 ** (77 / 160) + 100 ** (83 / 160)) / 2
        self.assertGreater(abs(row["x"] - wrong_edge_mean), 0.01)

    def test_circle_ring_square_and_cross(self):
        for shape, declared, fill_only in [("square", "square", True), ("circle", "circle", False),
                                            ("ring", "circle", True), ("cross", "cross", False)]:
            with self.subTest(shape=shape):
                doc, page = document(shape, fill_only=fill_only)
                config = configuration(series=[{"name": "signal", "color": [0, 0, 1],
                                                 "marker_shape": declared, "marker_path_indices": [0]}])
                with doc:
                    row = markers.extract(page, config)["signal"][0]
                self.assertEqual((row["pdf_x"], row["pdf_y"]), (100, 100))

    def test_wrong_shape_and_grouped_shapes_rejected(self):
        doc, page = document("square")
        config = configuration()
        config["series"][0]["marker_shape"] = "circle"
        with doc, self.assertRaises(markers.ExtractionError):
            markers.extract(page, config)
        doc = fitz.open()
        page = doc.new_page(width=200, height=200)
        pen = page.new_shape()
        pen.draw_rect((80, 90, 86, 96))
        pen.draw_rect((120, 90, 126, 96))
        pen.finish(color=(0, 0, 1))
        pen.commit()
        with doc, self.assertRaisesRegex(markers.ExtractionError, "one rectangle"):
            markers.extract(page, configuration())

    def test_ellipse_and_open_circle_rejected(self):
        doc = fitz.open()
        page = doc.new_page(width=200, height=200)
        page.draw_oval((90, 95, 110, 105), color=(0, 0, 1))
        config = configuration()
        config["series"][0]["marker_shape"] = "circle"
        with doc, self.assertRaisesRegex(markers.ExtractionError, "elliptical"):
            markers.extract(page, config)
        doc, page = document("circle")
        with doc:
            d = page.get_drawings()[0]
            d["items"] = d["items"][:-1]
            with self.assertRaisesRegex(markers.ExtractionError, "one circle"):
                markers.marker_center(d, "circle", 0.02)

    def test_cross_rejects_unequal_or_misaligned_strokes(self):
        doc, page = document("cross")
        with doc:
            drawing = page.get_drawings()[0]
            drawing["items"][1] = ("l", fitz.Point(97, 103), fitz.Point(104, 96))
            with self.assertRaisesRegex(markers.ExtractionError, "shared midpoint"):
                markers.marker_center(drawing, "cross", 0.02)

    def test_native_clip_rejects_partial_marker_despite_visible_center(self):
        doc = fitz.open()
        page = doc.new_page(width=200, height=200)
        page.draw_line((0, 0), (1, 1))
        doc.update_stream(page.get_contents()[0], b"q 99 40 61 120 re W n 0 0 1 RG 97 97 6 6 re S Q")
        config = configuration()
        config["series"][0]["marker_path_indices"] = [1]
        with doc, self.assertRaisesRegex(markers.ExtractionError, "clipped"):
            markers.extract(page, config)

    def test_panel_stroke_width_and_exclusion_rejected(self):
        doc, page = document(center=(23.25, 100))
        with doc, self.assertRaisesRegex(markers.ExtractionError, "clipped"):
            markers.extract(page, configuration())
        doc, page = document()
        with doc, self.assertRaisesRegex(markers.ExtractionError, "exclusion"):
            markers.extract(page, configuration(exclude_rects=[[102, 98, 110, 102]]))

    def test_hairline_error_cap_crossing_exclusion_is_rejected(self):
        # PDF width 0 is a visible device-pixel hairline, despite zero-area bounds.
        doc, page = document()
        page.draw_line((95, 110), (105, 110), color=(0, 0, 1))
        page.draw_line((95, 90), (105, 90), color=(0, 0, 1))
        # Write explicit hairline operators: draw_line(width=0) can omit them.
        doc.update_stream(page.get_contents()[1], b"q 0 w 0 0 1 RG 95 90 m 105 90 l S Q")
        doc.update_stream(page.get_contents()[2], b"q 0 w 0 0 1 RG 95 110 m 105 110 l S Q")
        config = configuration(exclude_rects=[[103, 108, 107, 112]])
        config["series"][0].update(lower_cap_path_indices=[1], upper_cap_path_indices=[2])
        with doc:
            self.assertEqual(page.get_drawings()[1]["width"], 0)
            with self.assertRaisesRegex(markers.ExtractionError, "exclusion"):
                markers.extract(page, config)

    def test_explicit_error_caps_and_reversed_axis(self):
        doc, page = document()
        page.draw_line((95, 110), (105, 110), color=(0, 0, 1))
        page.draw_line((95, 90), (105, 90), color=(0, 0, 1))
        config = configuration()
        config["series"][0].update(lower_cap_path_indices=[1], upper_cap_path_indices=[2])
        with doc:
            row = markers.extract(page, config)["signal"][0]
            self.assertAlmostEqual(row["y_lower"], 10 ** (-0.25))
            self.assertAlmostEqual(row["y_upper"], 10 ** 0.25)
            config["y_axis"]["limits"].reverse()
            with self.assertRaisesRegex(markers.ExtractionError, "bracket"):
                markers.extract(page, config)
            config["series"][0].update(lower_cap_path_indices=[2], upper_cap_path_indices=[1])
            row = markers.extract(page, config)["signal"][0]
            self.assertLess(row["y_lower"], row["y_upper"])

    def test_cap_pairing_requires_horizontal_aligned_strokes(self):
        for first, last in [((94, 110), (104, 110)), ((95, 108), (105, 112))]:
            doc, page = document()
            page.draw_line(first, last, color=(0, 0, 1))
            page.draw_line((95, 90), (105, 90), color=(0, 0, 1))
            config = configuration()
            config["series"][0].update(lower_cap_path_indices=[1], upper_cap_path_indices=[2])
            with doc, self.assertRaisesRegex(markers.ExtractionError, "horizontal and centered"):
                markers.extract(page, config)

    def test_missing_indices_and_ambiguous_selection(self):
        doc, page = document()
        config = configuration()
        config["series"][0]["marker_path_indices"] = [99]
        with doc, self.assertRaisesRegex(markers.ExtractionError, "missing"):
            markers.extract(page, config)
        for changes in ({"marker_path_indices": [0, 0]}, {"lower_cap_path_indices": [1]},
                        {"lower_cap_path_indices": [1], "upper_cap_path_indices": [2, 3]},
                        {"lower_cap_path_indices": [0], "upper_cap_path_indices": [2]}):
            config = configuration()
            config["series"][0].update(changes)
            with self.assertRaises(markers.ExtractionError):
                markers.validate_config(config)

    def test_end_to_end_outputs_hashes_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            doc, page = document("ring", fill_only=True)
            with doc:
                doc.save(root / "source.pdf")
            config = configuration()
            config["series"][0]["marker_shape"] = "circle"
            config.update(pdf="source.pdf", output_prefix="out/result", dpi=72)
            cfg = root / "figure.json"
            cfg.write_text(json.dumps(config))
            self.assertEqual(markers.main(["--config", str(cfg)]), 0)
            files = list((root / "out").iterdir())
            before = {p.name: p.read_bytes() for p in files}
            self.assertEqual(markers.main(["--config", str(cfg)]), 2)
            self.assertEqual({p.name: p.read_bytes() for p in files}, before)
            manifest = json.loads((root / "out/result_manifest.json").read_text())
            self.assertEqual(manifest["review_status"], "pending_manual_review")
            self.assertIn("calibration_diagnostics", manifest)
            self.assertNotIn("calibration_checks", manifest)
            self.assertNotIn(str(root), json.dumps(manifest))
            for key, item in manifest["outputs"].items():
                suffix = "csv" if key == "signal" else "png"
                self.assertEqual(item["sha256"], hashlib.sha256((root / f"out/result_{key}.{suffix}").read_bytes()).hexdigest())
            with (root / "out/result_signal.csv").open() as stream:
                row = next(csv.DictReader(stream))
            self.assertAlmostEqual(float(row["x"]), 10)

    def test_source_clip_preserves_calibration_and_native_overlay_registration(self):
        from PIL import Image
        doc, page = document()
        config = configuration(source_clip=[0, 0, 200, 200], dpi=72)
        with doc, tempfile.TemporaryDirectory() as tmp:
            result = markers.extract(page, config)
            self.assertEqual(result, markers.extract(page, configuration()))
            prefix = Path(tmp) / "source-context"
            manifest = markers.write_outputs(page, config, result, doc.tobytes(), prefix)
            original = Image.open(str(prefix) + "_panel.png")
            overlay = Image.open(str(prefix) + "_validation.png")
            self.assertEqual(original.size, (200, 200))
            # At 72 DPI, marker center is (100,100) in the wider source crop.
            self.assertEqual(overlay.getpixel((103, 100)), (255, 0, 200))
            self.assertEqual(overlay.getpixel((83, 80)), original.getpixel((83, 80)))
            self.assertEqual(manifest["rendering"]["source_clip"], [0, 0, 200, 200])
            config["source_clip"] = [0, 0, 201, 200]
            with self.assertRaisesRegex(markers.ExtractionError, "source_clip lies outside"):
                markers.extract(page, config)

    def test_invalid_extraction_writes_no_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            doc, page = document()
            with doc:
                doc.save(root / "source.pdf")
            config = configuration()
            config["series"][0]["marker_shape"] = "cross"
            config.update(pdf="source.pdf", output_prefix="out/result")
            cfg = root / "figure.json"
            cfg.write_text(json.dumps(config))
            self.assertEqual(markers.main(["--config", str(cfg)]), 2)
            self.assertFalse((root / "out").exists())


if __name__ == "__main__":
    unittest.main()
