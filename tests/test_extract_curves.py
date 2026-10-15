"""Known-vector regressions: geometry and calibration, not plausibility checks."""
import csv
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import tempfile
import unittest

import fitz
from PIL import Image

SPEC = importlib.util.spec_from_file_location("extract_curves", Path(__file__).resolve().parents[1] / "extract_curves.py")
curves = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(curves)


def configuration(**overrides):
    config = {"page": 1, "panel": [20, 20, 180, 180],
              "x_axis": {"scale": "linear", "limits": [0, 1000]},
              "y_axis": {"scale": "linear", "limits": [0, 10]},
              "series": [{"name": "signal", "color": [0, 0, 1]}]}
    config.update(overrides)
    return curves.validate_config(config)


def document(lines, color=(0, 0, 1)):
    doc = fitz.open()
    page = doc.new_page(width=200, height=200)
    for points in lines:
        shape = page.new_shape()
        shape.draw_polyline(points)
        shape.finish(color=color, closePath=False)
        shape.commit()
    return doc, page


class ExtractionTests(unittest.TestCase):
    def test_linear_axes_and_reversed_limits(self):
        c = configuration()
        self.assertEqual(curves.data_point((20, 180), c), (0, 0))
        self.assertEqual(curves.data_point((180, 20), c), (1000, 10))
        self.assertEqual(curves.data_point((100, 100), c), (500, 5))
        c["x_axis"]["limits"] = [1000, 0]
        c["y_axis"]["limits"] = [10, 0]
        self.assertEqual(curves.data_point((20, 180), c), (1000, 10))

    def test_both_log_axes_and_exact_endpoints(self):
        c = configuration(x_axis={"scale": "log", "limits": [1, 1000]},
                          y_axis={"scale": "log", "limits": [0.01, 100]})
        self.assertEqual(curves.data_point((180, 20), c), (1000, 100))
        x, y = curves.data_point((100, 100), c)
        self.assertAlmostEqual(x, math.sqrt(1000))
        self.assertAlmostEqual(y, 1)
        c["x_axis"]["limits"].reverse()
        self.assertEqual(curves.data_point((20, 20), c)[0], 1000)
        with self.assertRaises(curves.ExtractionError):
            configuration(x_axis={"scale": "log", "limits": [0, 100]})

    def test_same_color_crossing_paths_stay_separate(self):
        doc, page = document([[(20, 180), (180, 20)], [(20, 20), (180, 180)]])
        with doc:
            result, warnings = curves.extract(page, configuration())
        self.assertEqual(len(result["signal"]), 2)
        self.assertEqual([s["points"][-1][1] for s in result["signal"]], [20, 180])
        self.assertNotEqual(result["signal"][0]["path_index"], result["signal"][1]["path_index"])
        self.assertTrue(warnings)

    def test_disconnected_subpaths_within_one_drawing(self):
        doc = fitz.open()
        page = doc.new_page(width=200, height=200)
        shape = page.new_shape()
        shape.draw_line((20, 160), (60, 120))
        shape.draw_line((130, 60), (180, 20))
        shape.finish(color=(0, 0, 1), closePath=False)
        shape.commit()
        with doc:
            result, _ = curves.extract(page, configuration())
        self.assertEqual(len(result["signal"]), 2)
        self.assertEqual(result["signal"][0]["points"][-1], (60, 120))
        self.assertEqual(result["signal"][1]["points"][0], (130, 60))

    def test_explicit_closed_stroke_retains_closing_edge(self):
        drawing = {"items": [("l", (20, 160), (100, 20)),
                              ("l", (100, 20), (180, 160))], "closePath": True}
        chains = curves.path_polylines(drawing, 0.05)
        self.assertEqual(chains[0], [(20, 160), (100, 20), (180, 160), (20, 160)])
        drawing["items"].append(("l", (40, 50), (60, 80)))
        with self.assertRaisesRegex(curves.ExtractionError, "ambiguous subpaths"):
            curves.path_polylines(drawing, 0.05)
        doc = fitz.open()
        page = doc.new_page(width=200, height=200)
        page.draw_polyline([(20, 160), (100, 20), (180, 160)], color=(0, 0, 1), closePath=True)
        with doc:
            extracted, _ = curves.extract(page, configuration())
        self.assertEqual(extracted["signal"][0]["points"][0], extracted["signal"][0]["points"][-1])

    def test_invalid_optional_input_types_rejected(self):
        for override in ({"exclude_rects": None}, {"output_prefix": 42}, {"output_prefix": ""}):
            with self.subTest(override=override), self.assertRaises(curves.ExtractionError):
                configuration(**override)

    def test_panel_clipping_interpolates_boundaries_without_dropping_line(self):
        doc, page = document([[(0, 100), (200, 100)]])
        with doc:
            result, _ = curves.extract(page, configuration())
        self.assertEqual(result["signal"][0]["points"], [(20, 100), (180, 100)])

    def test_legend_exclusion_and_excluded_gap_are_not_bridged(self):
        doc, page = document([[(20, 150), (180, 150)], [(40, 40), (80, 40)]])
        config = configuration(exclude_rects=[[30, 30, 90, 50], [90, 140, 110, 160]])
        with doc:
            result, _ = curves.extract(page, config)
        self.assertEqual(len(result["signal"]), 2)
        self.assertEqual(result["signal"][0]["points"][-1], (90, 150))
        self.assertEqual(result["signal"][1]["points"][0], (110, 150))

    def test_endpoint_not_median_collapsed_with_same_color_artifact(self):
        # The short lower stroke must never replace the real curve's endpoint.
        doc, page = document([[(20, 180), (180, 100)], [(179, 136.32), (180, 136.32)]])
        config = configuration()
        with doc:
            result, _ = curves.extract(page, config)
        endpoints = [curves.data_point(s["points"][-1], config) for s in result["signal"]]
        self.assertEqual(endpoints[0], (1000, 5))
        self.assertAlmostEqual(endpoints[1][1], 2.73, places=5)
        config["series"][0]["path_indices"] = [0]
        with fitz.open(stream=doc_bytes_for_lines(), filetype="pdf") as source:
            selected, _ = curves.extract(source[0], config)
        self.assertEqual(len(selected["signal"]), 1)

    def test_missing_series_writes_nothing_and_returns_nonzero(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            doc, _ = document([[(20, 180), (180, 20)]], color=(1, 0, 0))
            with doc:
                doc.save(root / "source.pdf")
            config = configuration()
            config.update(pdf="source.pdf", output_prefix="out/check")
            (root / "figure.json").write_text(json.dumps(config))
            self.assertEqual(curves.main(["--config", str(root / "figure.json")]), 2)
            self.assertFalse((root / "out").exists())

    def test_cubic_preserves_endpoints_and_collinear_turns(self):
        pts = curves.bezier_points((20, 100), (60, 20), (140, 180), (180, 100), 0.05)
        self.assertEqual(pts[0], (20, 100))
        self.assertEqual(pts[-1], (180, 100))
        self.assertGreater(len(pts), 4)
        turn = curves.bezier_points((20, 100), (180, 100), (0, 100), (40, 100), 0.05)
        self.assertGreater(max(p[0] for p in turn), 40)
        doc = fitz.open()
        page = doc.new_page(width=200, height=200)
        shape = page.new_shape()
        shape.draw_bezier((20, 100), (60, 20), (140, 180), (180, 100))
        shape.finish(color=(0, 0, 1), closePath=False)
        shape.commit()
        with doc:
            result, _ = curves.extract(page, configuration())
        self.assertEqual(len(result["signal"]), 1)
        self.assertGreater(len(result["signal"][0]["points"]), 4)

    def test_overlapping_series_selection_rejected(self):
        doc, page = document([[(20, 180), (180, 20)]])
        config = configuration(series=[{"name": "one", "color": [0, 0, 1]},
                                       {"name": "two", "color": [0, 0, 1]}])
        with doc, self.assertRaisesRegex(curves.ExtractionError, "multiple series"):
            curves.extract(page, config)

    def test_rotated_page_rejected(self):
        doc, page = document([[(20, 180), (180, 20)]])
        page.set_rotation(90)
        with doc, self.assertRaisesRegex(curves.ExtractionError, "rotated"):
            curves.extract(page, configuration())

    def test_native_rectangle_clip_respected(self):
        doc = fitz.open()
        page = doc.new_page(width=200, height=200)
        page.draw_line((0, 0), (1, 1))  # Create a content stream.
        doc.update_stream(page.get_contents()[0], b"q 40 40 120 120 re W n 0 0 1 RG 0 100 m 200 100 l S Q")
        with doc:
            result, _ = curves.extract(page, configuration())
        self.assertEqual(result["signal"][0]["points"], [(40, 100), (160, 100)])

    def test_nonrectangular_clip_rejected(self):
        doc = fitz.open()
        page = doc.new_page(width=200, height=200)
        page.draw_line((0, 0), (1, 1))
        doc.update_stream(page.get_contents()[0], b"q 40 40 m 160 40 l 100 160 l h W n 0 0 1 RG 0 100 m 200 100 l S Q")
        with doc, self.assertRaisesRegex(curves.ExtractionError, "nonrectangular"):
            curves.extract(page, configuration())

    def test_precision_and_manifest_and_native_overlay(self):
        with tempfile.TemporaryDirectory() as tmp:
            prefix = Path(tmp) / "sample"
            doc, page = document([[(20, 160), (180, 40)], [(20, 40), (180, 160)]])
            config = configuration(x_axis={"scale": "log", "limits": [1e-7, 1e-3]}, dpi=144)
            with doc:
                extracted, warnings = curves.extract(page, config)
                manifest = curves.write_outputs(page, config, extracted, doc.tobytes(), prefix, warnings)
                with self.assertRaisesRegex(curves.ExtractionError, "already exists"):
                    curves.write_outputs(page, config, extracted, doc.tobytes(), prefix, warnings)
            with Path(f"{prefix}_signal.csv").open() as stream:
                rows = list(csv.DictReader(stream))
            self.assertEqual(float(rows[0]["x"]), 1e-7)
            self.assertEqual(float(rows[1]["x"]), 1e-3)
            self.assertEqual({r["segment_id"] for r in rows}, {"1", "2"})
            self.assertFalse(manifest["fitting_applied"])
            self.assertEqual(manifest["review_status"], "pending_manual_review")
            serialized = json.dumps(manifest)
            self.assertNotIn(str(prefix), serialized)
            self.assertEqual(len(manifest["source_sha256"]), 64)
            self.assertEqual(len(manifest["calibration_sha256"]), 64)
            self.assertEqual(manifest["rendering"]["dpi"], 144)
            self.assertEqual(set(manifest["outputs"]), {"signal", "panel", "validation", "extracted"})
            self.assertEqual(manifest["outputs"]["signal"]["sha256"],
                             hashlib.sha256(Path(f"{prefix}_signal.csv").read_bytes()).hexdigest())
            for key in ("panel", "validation", "extracted"):
                self.assertEqual(manifest["outputs"][key]["sha256"],
                                 hashlib.sha256(Path(f"{prefix}_{key}.png").read_bytes()).hexdigest())
            with Image.open(f"{prefix}_validation.png") as image:
                self.assertEqual(image.size, (320, 320))
                self.assertEqual(image.getpixel((160, 160)), (255, 0, 200))

    def test_native_pdf_dash_style_is_kept(self):
        doc = fitz.open()
        page = doc.new_page(width=200, height=200)
        page.draw_line((20, 100), (180, 100), color=(0, 0, 1), dashes="[3 2] 0")
        config = configuration()
        config["series"][0]["stroke_style"] = "dashed"
        with doc:
            result, _ = curves.extract(page, config)
        self.assertEqual(result["signal"][0]["stroke_style"], "dashed")


def doc_bytes_for_lines():
    doc, _ = document([[(20, 180), (180, 100)], [(179, 136.32), (180, 136.32)]])
    with doc:
        return doc.tobytes()


if __name__ == "__main__":
    unittest.main()
