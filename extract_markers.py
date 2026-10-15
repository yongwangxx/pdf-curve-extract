#!/usr/bin/env python3
"""Recover centers of explicitly selected, geometrically checked PDF markers."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import sys
from pathlib import Path

import fitz
import extract_curves as curves

ExtractionError = curves.ExtractionError


def path_ids(value, label):
    if (not isinstance(value, list) or not value or
            any(isinstance(i, bool) or not isinstance(i, int) or i < 0 for i in value) or
            len(set(value)) != len(value)):
        raise ExtractionError(f"{label} must contain unique nonnegative path indices")
    return value


def validate_config(config):
    out = curves.validate_config(config)
    out["marker_tolerance"] = curves.finite_number(config.get("marker_tolerance", 0.02), "marker_tolerance")
    if not 0 < out["marker_tolerance"] <= 0.1:
        raise ExtractionError("marker_tolerance must be positive and at most 0.1 PDF point")
    used = set()
    for s in out["series"]:
        if s.get("marker_shape") not in ("square", "circle", "cross"):
            raise ExtractionError("marker_shape must explicitly be square, circle, or cross")
        ids = path_ids(s.get("marker_path_indices"), "marker_path_indices")
        if "path_indices" in s or "stroke_style" in s:
            raise ExtractionError("use marker_path_indices without stroke selectors for marker extraction")
        lower, upper = s.get("lower_cap_path_indices"), s.get("upper_cap_path_indices")
        if (lower is None) != (upper is None):
            raise ExtractionError("provide both lower_cap_path_indices and upper_cap_path_indices")
        all_ids = list(ids)
        if lower is not None:
            for key in ("lower_cap_path_indices", "upper_cap_path_indices"):
                caps = path_ids(s[key], key)
                if len(caps) != len(ids):
                    raise ExtractionError("cap arrays must contain one path index per marker, in matching order")
                all_ids.extend(caps)
        if len(set(all_ids)) != len(all_ids) or used.intersection(all_ids):
            raise ExtractionError("selected marker and cap paths must be disjoint")
        used.update(all_ids)
    return out


def near(a, b, tol):
    return math.dist(a, b) <= tol


def midpoint(a, b):
    return ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)


def bounds_points(points):
    return (min(p[0] for p in points), min(p[1] for p in points),
            max(p[0] for p in points), max(p[1] for p in points))


def circle_loop(items, tol):
    if len(items) not in (4, 8):
        raise ExtractionError("a circle must contain a closed loop of four or eight cubic arcs")
    starts = [curves.point(i[1]) for i in items]
    bounds = bounds_points(starts)
    cx, cy = midpoint(bounds[:2], bounds[2:])
    rx, ry = (bounds[2] - bounds[0]) / 2, (bounds[3] - bounds[1]) / 2
    if min(rx, ry) <= 5 * tol or abs(rx - ry) > tol:
        raise ExtractionError("selected circle is too small, nonsymmetric, or elliptical")
    radius = (rx + ry) / 2
    orientation = None
    for number, item in enumerate(items):
        p0, p1, p2, p3 = [curves.point(p) for p in item[1:5]]
        if not near(p3, starts[(number + 1) % len(items)], tol):
            raise ExtractionError("circle arcs do not form one closed, connected loop")
        a = (p0[0] - cx, p0[1] - cy)
        b = (p3[0] - cx, p3[1] - cy)
        angle = math.atan2(a[0] * b[1] - a[1] * b[0], a[0] * b[0] + a[1] * b[1])
        sign = 1 if angle > 0 else -1
        if orientation is not None and sign != orientation:
            raise ExtractionError("circle arcs have inconsistent orientation")
        orientation = sign
        if abs(abs(angle) - 2 * math.pi / len(items)) * radius > 2 * tol:
            raise ExtractionError("circle arcs must have equal angular spans")
        for p in (p0, p3):
            if abs(math.dist(p, (cx, cy)) - radius) > tol:
                raise ExtractionError("circle endpoints do not share a radius")
        for endpoint, control in ((p0, p1), (p3, p2)):
            radial = (endpoint[0] - cx, endpoint[1] - cy)
            tangent = (control[0] - endpoint[0], control[1] - endpoint[1])
            if abs(radial[0] * tangent[0] + radial[1] * tangent[1]) / radius > tol:
                raise ExtractionError("circle controls are not tangential")
        for p in curves.bezier_points(p0, p1, p2, p3, min(tol / 4, radius * 0.0002)):
            if abs(math.dist(p, (cx, cy)) - radius) > tol:
                raise ExtractionError("selected cubic geometry is not circular")
    return (cx, cy), radius, bounds, orientation


def marker_center(drawing, shape, tol):
    items = drawing.get("items", [])
    if curves.stroke_style(drawing) != "solid":
        raise ExtractionError("dashed marker geometry is not supported")
    if shape == "square":
        if len(items) != 1 or items[0][0] != "re":
            raise ExtractionError("a square marker must be one rectangle primitive")
        rect = tuple(items[0][1])
        width, height = rect[2] - rect[0], rect[3] - rect[1]
        if min(width, height) <= 10 * tol or abs(width - height) > tol:
            raise ExtractionError("selected square is too small or nonsymmetric")
        return midpoint(rect[:2], rect[2:]), rect
    if shape == "cross":
        if len(items) != 2 or any(i[0] != "l" for i in items) or drawing.get("closePath"):
            raise ExtractionError("a cross marker must contain two independent straight strokes")
        a, b, c, d = [curves.point(p) for i in items for p in i[1:3]]
        ma, mb = midpoint(a, b), midpoint(c, d)
        la, lb = math.dist(a, b), math.dist(c, d)
        if min(la, lb) <= 10 * tol or not near(ma, mb, tol) or abs(la - lb) > tol:
            raise ExtractionError("cross strokes must have a shared midpoint and equal lengths")
        if abs((b[0]-a[0])*(d[0]-c[0]) + (b[1]-a[1])*(d[1]-c[1])) / la > tol:
            raise ExtractionError("cross strokes must be perpendicular")
        if drawing.get("color") is None or (drawing.get("stroke_opacity") or 0) <= 0:
            raise ExtractionError("a cross must have visible strokes")
        return midpoint(ma, mb), bounds_points((a, b, c, d))
    if not items or any(i[0] != "c" for i in items):
        raise ExtractionError("a circle marker must consist only of cubic arcs")
    loops, chain = [], []
    for item in items:
        if chain and not near(curves.point(chain[-1][4]), curves.point(item[1]), tol):
            raise ExtractionError("circle path has a disconnected or incomplete loop")
        chain.append(item)
        if near(curves.point(item[4]), curves.point(chain[0][1]), tol):
            loops.append(circle_loop(chain, tol))
            chain = []
    if chain or len(loops) not in (1, 2):
        raise ExtractionError("circle path must contain one circle or two concentric ring boundaries")
    if len(loops) == 2:
        if not near(loops[0][0], loops[1][0], tol) or abs(loops[0][1] - loops[1][1]) <= 2 * tol:
            raise ExtractionError("ring boundaries must be distinct and concentric")
        if drawing.get("fill") is None or (drawing.get("fill_opacity") or 0) <= 0:
            raise ExtractionError("a two-loop ring must have visible fill")
        if not drawing.get("even_odd") and loops[0][3] == loops[1][3]:
            raise ExtractionError("nonzero-fill ring boundaries must have opposite orientations")
    outer = max(loops, key=lambda item: item[1])
    return outer[0], outer[2]


def paint_matches(drawing, color, tolerance):
    for key, opacity in (("color", "stroke_opacity"), ("fill", "fill_opacity")):
        value = drawing.get(key)
        if (value is not None and len(value) == 3 and (drawing.get(opacity) or 0) > 0 and
                all(abs(a-b) <= tolerance for a, b in zip(value, color))):
            return True
    return False


def visible_bounds(drawing, bounds):
    half_width = ((drawing.get("width") or 0) / 2
                  if drawing.get("color") is not None and (drawing.get("stroke_opacity") or 0) > 0 else 0)
    return (bounds[0] - half_width, bounds[1] - half_width,
            bounds[2] + half_width, bounds[3] + half_width)


def fully_visible(drawing, bounds, clips, config):
    box = visible_bounds(drawing, bounds)
    for clip in [config["panel"], *clips]:
        if clip is None:
            raise ExtractionError("selected marker or cap uses a nonrectangular PDF clip")
        if not (clip[0] <= box[0] and clip[1] <= box[1] and box[2] <= clip[2] and box[3] <= clip[3]):
            raise ExtractionError("selected marker or cap is clipped; its complete geometry must be visible")
    if any(box[0] <= exclusion[2] and exclusion[0] <= box[2] and
           box[1] <= exclusion[3] and exclusion[1] <= box[3]
           for exclusion in config["exclude_rects"]):
        raise ExtractionError("selected marker or cap intersects an exclusion rectangle")


def cap_position(drawing, center, tol):
    items = drawing.get("items", [])
    if (len(items) != 1 or items[0][0] != "l" or drawing.get("closePath") or
            curves.stroke_style(drawing) != "solid" or drawing.get("color") is None or
            (drawing.get("stroke_opacity") or 0) <= 0):
        raise ExtractionError("an error-bar cap must be one visible solid horizontal straight stroke")
    a, b = curves.point(items[0][1]), curves.point(items[0][2])
    middle = midpoint(a, b)
    if abs(a[1] - b[1]) > tol or abs(a[0] - b[0]) <= 10 * tol or abs(middle[0] - center[0]) > tol:
        raise ExtractionError("error-bar cap must be horizontal and centered on its paired marker")
    return middle, bounds_points((a, b))


def extract(page, config):
    drawings = curves.get_drawings(page)
    curves.validate_page_regions(page, config)
    wanted = {i for s in config["series"] for key in
              ("marker_path_indices", "lower_cap_path_indices", "upper_cap_path_indices") for i in s.get(key, [])}
    selected, active_clips, active_groups = {}, {}, {}
    for index, drawing in enumerate(drawings):
        level = drawing.get("level", 0)
        active_clips = {k:v for k,v in active_clips.items() if k < level}
        active_groups = {k:v for k,v in active_groups.items() if k < level}
        if drawing["type"] == "clip":
            items = drawing.get("items", [])
            active_clips[level] = tuple(items[0][1]) if len(items) == 1 and items[0][0] == "re" else None
        elif drawing["type"] == "group":
            active_groups[level] = drawing.get("opacity", 1)
        elif index in wanted:
            if any(v <= 0 for v in active_groups.values()):
                raise ExtractionError("selected geometry is in an invisible PDF group")
            selected[index] = (drawing, list(active_clips.values()))
    if wanted.difference(selected):
        raise ExtractionError("selected path indices are missing or do not refer to painted paths")
    result = {}
    for series in config["series"]:
        rows = []
        for n, index in enumerate(series["marker_path_indices"]):
            drawing, clips = selected[index]
            if not paint_matches(drawing, series["color"], config["color_tolerance"]):
                raise ExtractionError(f"marker path {index} does not have the selected visible color")
            center, bounds = marker_center(drawing, series["marker_shape"], config["marker_tolerance"])
            fully_visible(drawing, bounds, clips, config)
            x, y = curves.data_point(center, config)
            row = {"marker_id": n + 1, "path_index": index, "x": x, "y": y,
                   "pdf_x": center[0], "pdf_y": center[1]}
            if "lower_cap_path_indices" in series:
                caps = {}
                for side in ("lower", "upper"):
                    cap_index = series[f"{side}_cap_path_indices"][n]
                    cap, cap_clips = selected[cap_index]
                    if not paint_matches(cap, series["color"], config["color_tolerance"]):
                        raise ExtractionError(f"cap path {cap_index} does not have the selected visible color")
                    position, cap_bounds = cap_position(cap, center, config["marker_tolerance"])
                    fully_visible(cap, cap_bounds, cap_clips, config)
                    value = curves.data_point(position, config)[1]
                    caps[side] = value
                    row.update({f"{side}_cap_path_index": cap_index, f"y_{side}": value,
                                f"{side}_cap_pdf_y": position[1]})
                if not caps["lower"] < y < caps["upper"]:
                    raise ExtractionError("paired lower and upper caps must bracket the marker in data values")
            rows.append(row)
        result[series["name"]] = rows
    return result


def output_paths(prefix, config):
    paths = {s["name"]: Path(f"{prefix}_{s['name']}.csv") for s in config["series"]}
    paths.update({k: Path(f"{prefix}_{k}.{ext}") for k, ext in
                  (("panel", "png"), ("validation", "png"), ("extracted", "png"),
                   ("comparison", "png"), ("manifest", "json"))})
    if len(paths) != len(config["series"]) + 5:
        raise ExtractionError("series name conflicts with an output name")
    return paths


def safe_manifest(config, source_bytes, extracted):
    calibration = {k: config[k] for k in ("page", "panel", "x_axis", "y_axis", "exclude_rects",
                                         "color_tolerance", "marker_tolerance")}
    keys = ("name", "color", "marker_shape", "marker_path_indices",
            "lower_cap_path_indices", "upper_cap_path_indices")
    calibration["series"] = [{k: s[k] for k in keys if k in s} for s in config["series"]]
    return {"schema_version": 1, "extractor_version": "2.1.0", "extraction_type": "reviewed_markers",
            "source_sha256": hashlib.sha256(source_bytes).hexdigest(), "calibration": calibration,
            "calibration_sha256": hashlib.sha256(json.dumps(calibration, sort_keys=True,
                                                  separators=(",", ":")).encode()).hexdigest(),
            "series": {name: {"point_count": len(rows), "path_indices": [r["path_index"] for r in rows],
                              "x_range": [min(r["x"] for r in rows), max(r["x"] for r in rows)],
                              "y_range": [min(r["y"] for r in rows), max(r["y"] for r in rows)]}
                       for name, rows in extracted.items()},
            "review_status": "pending_manual_review", "fitting_applied": False,
            "point_interpretation": "Validated marker centers transformed from PDF coordinates",
            "error_bar_interpretation": "Geometric endpoints only; statistical definition is not inferred",
            "limitations": ["Path selection and scientific meaning require manual review",
                            "Later overpainting or raster occlusion is not detected; inspect the source crop",
                            "Only explicitly selected square, circular and cross geometry is supported"],
            "rendering": {"dpi": config["dpi"], "source_clip": list(config.get("source_clip", config["panel"])),
                          "source_clip_units": "PDF_points"},
            "pymupdf_version": fitz.VersionBind}


def write_outputs(page, config, extracted, source_bytes, prefix):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from PIL import Image, ImageDraw
    from review_images import save_comparison

    paths = output_paths(prefix, config)
    if any(p.exists() for p in paths.values()):
        raise ExtractionError("output already exists; choose a new output_prefix to preserve the previous extraction")
    for path in paths.values():
        path.parent.mkdir(parents=True, exist_ok=True)
    scale = config["dpi"] / 72
    pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), clip=fitz.Rect(config.get("source_clip", config["panel"])),
                         colorspace=fitz.csRGB, alpha=False)
    original = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    overlay = original.copy()
    draw = ImageDraw.Draw(overlay)
    fig, ax = plt.subplots(figsize=(7, 5))
    marker_styles = {"circle": "o", "square": "s", "cross": "x"}
    for series in config["series"]:
        name = series["name"]
        rows = extracted[name]
        with paths[name].open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
        x, y = [r["x"] for r in rows], [r["y"] for r in rows]
        errors = ([[r["y"] - r["y_lower"] for r in rows], [r["y_upper"] - r["y"] for r in rows]]
                  if "y_lower" in rows[0] else None)
        ax.errorbar(x, y, yerr=errors, fmt=marker_styles[series["marker_shape"]], linestyle="none",
                    color=series["color"], markersize=5, capsize=3, label=name)
        for r in rows:
            px, py = r["pdf_x"] * scale - pix.x, r["pdf_y"] * scale - pix.y
            draw.ellipse((px-3, py-3, px+3, py+3), outline=(255, 0, 200), width=2)
            if "y_lower" in r:
                draw.line((px, r["lower_cap_pdf_y"] * scale - pix.y,
                           px, r["upper_cap_pdf_y"] * scale - pix.y), fill=(255, 0, 200), width=1)
    for axis in ("x", "y"):
        getattr(ax, f"set_{axis}scale")(config[f"{axis}_axis"]["scale"])
        limits = curves.axis_limits(config, axis) if hasattr(curves, "axis_limits") else config[f"{axis}_axis"]["limits"]
        getattr(ax, f"set_{axis}lim")(*limits)
        getattr(ax, f"set_{axis}label")(axis)
    ax.legend()
    ax.set_title("Extracted marker centers")
    fig.tight_layout()
    fig.savefig(paths["extracted"], dpi=config["dpi"])
    plt.close(fig)
    original.save(paths["panel"])
    overlay.save(paths["validation"])
    save_comparison(paths["panel"], paths["extracted"], paths["comparison"])
    manifest = safe_manifest(config, source_bytes, extracted)
    if hasattr(curves, "calibration_diagnostics"):
        manifest["calibration_diagnostics"] = curves.calibration_diagnostics(config)
    manifest["outputs"] = {k: {"sha256": hashlib.sha256(p.read_bytes()).hexdigest()}
                           for k, p in paths.items() if k != "manifest"}
    paths["manifest"].write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path, help="JSON calibration and reviewed marker selection")
    parser.add_argument("--pdf", type=Path, help="source PDF override, relative to the current directory")
    parser.add_argument("--output-prefix", type=Path, help="output override, relative to the current directory")
    args = parser.parse_args(argv)
    try:
        config = validate_config(json.loads(args.config.read_text(encoding="utf-8")))
        base = args.config.resolve().parent
        source = args.pdf or (base / config["pdf"] if isinstance(config.get("pdf"), str) else None)
        if source is None:
            raise ExtractionError("provide --pdf or a pdf entry in the config")
        source_bytes = source.read_bytes()
        with fitz.open(stream=source_bytes, filetype="pdf") as doc:
            if config["page"] > len(doc):
                raise ExtractionError("page is outside the document")
            page = doc[config["page"]-1]
            extracted = extract(page, config)
            prefix = args.output_prefix or (base / config.get("output_prefix", "markers"))
            manifest = write_outputs(page, config, extracted, source_bytes, prefix)
            for name, summary in manifest["series"].items():
                print(f"{name}: {summary['point_count']} marker centers")
        print("Extraction complete. Review the source, marker selection, paired caps, and comparison before use.")
        return 0
    except (ExtractionError, json.JSONDecodeError) as exc:
        print(f"Extraction stopped: {exc}", file=sys.stderr)
        return 2
    except (OSError, RuntimeError) as exc:
        print(f"Extraction stopped: file or PDF operation failed ({type(exc).__name__}). Check inputs and permissions.",
              file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
