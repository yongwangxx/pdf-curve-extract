#!/usr/bin/env python3
"""Extract calibrated vector strokes without fitting or joining separate paths."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
import sys
from pathlib import Path

import fitz


class ExtractionError(ValueError):
    """An input cannot be extracted without an ambiguous or invalid result."""


def finite_number(value, label):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ExtractionError(f"{label} must be a finite number")
    return float(value)


def rectangle(values, label):
    if not isinstance(values, (list, tuple)) or len(values) != 4:
        raise ExtractionError(f"{label} must contain left, top, right, bottom")
    rect = tuple(finite_number(v, label) for v in values)
    if rect[0] >= rect[2] or rect[1] >= rect[3]:
        raise ExtractionError(f"{label} must have positive width and height")
    return rect


def axis_config(value, label):
    if not isinstance(value, dict) or value.get("scale") not in ("linear", "log"):
        raise ExtractionError(f"{label}.scale must explicitly be linear or log")
    limits = value.get("limits")
    if not isinstance(limits, (list, tuple)) or len(limits) != 2:
        raise ExtractionError(f"{label}.limits must contain two values")
    limits = [finite_number(v, label) for v in limits]
    if limits[0] == limits[1]:
        raise ExtractionError(f"{label}.limits must differ")
    if value["scale"] == "log" and min(limits) <= 0:
        raise ExtractionError(f"{label}: logarithmic limits must be positive")
    return {"scale": value["scale"], "limits": limits}


def validate_config(config):
    if not isinstance(config, dict):
        raise ExtractionError("configuration must be a JSON object")
    out = dict(config)
    out["panel"] = rectangle(config.get("panel"), "panel")
    for axis in ("x_axis", "y_axis"):
        out[axis] = axis_config(config.get(axis), axis)
    page = config.get("page")
    if isinstance(page, bool) or not isinstance(page, int) or page < 1:
        raise ExtractionError("page must be a positive, 1-based integer")
    exclusions = config.get("exclude_rects", [])
    if not isinstance(exclusions, list):
        raise ExtractionError("exclude_rects must be a list of rectangles")
    out["exclude_rects"] = [rectangle(r, "exclude_rects") for r in exclusions]
    if "output_prefix" in config and (not isinstance(config["output_prefix"], str) or not config["output_prefix"]):
        raise ExtractionError("output_prefix must be a nonempty string")
    for key, default in (("color_tolerance", 0.02), ("curve_tolerance", 0.05), ("dpi", 200)):
        out[key] = finite_number(config.get(key, default), key)
        if out[key] <= 0:
            raise ExtractionError(f"{key} must be positive")
    if out["color_tolerance"] > 1:
        raise ExtractionError("color_tolerance must be at most 1")
    if not isinstance(config.get("series"), list) or not config["series"]:
        raise ExtractionError("series must be a nonempty list")
    names = set()
    out["series"] = []
    for item in config["series"]:
        if not isinstance(item, dict):
            raise ExtractionError("each series must be an object")
        item = dict(item)
        name = item.get("name", "")
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", name):
            raise ExtractionError("series names must use letters, digits, underscores, or hyphens")
        if name in names:
            raise ExtractionError("series names must be unique")
        names.add(name)
        color = item.get("color")
        if not isinstance(color, (list, tuple)) or len(color) != 3:
            raise ExtractionError(f"series {name}: color must contain three RGB values")
        color = [finite_number(c, "color") for c in color]
        if min(color) < 0 or max(color) > 1:
            raise ExtractionError("RGB values must lie between 0 and 1")
        item["color"] = color
        if "stroke_style" in item and item["stroke_style"] not in ("solid", "dashed"):
            raise ExtractionError("stroke_style must be solid or dashed")
        if "path_indices" in item:
            indices = item["path_indices"]
            if (not isinstance(indices, list) or not indices or
                    any(isinstance(i, bool) or not isinstance(i, int) or i < 0 for i in indices)):
                raise ExtractionError("path_indices must be a nonempty list of nonnegative integers")
        out["series"].append(item)
    return out


def axis_value(fraction, axis):
    low, high = axis["limits"]
    if fraction == 0:
        return low
    if fraction == 1:
        return high
    if axis["scale"] == "log":
        return math.exp(math.log(low) + fraction * (math.log(high) - math.log(low)))
    return low + fraction * (high - low)


def data_point(point, config):
    left, top, right, bottom = config["panel"]
    return (axis_value((point[0] - left) / (right - left), config["x_axis"]),
            axis_value((bottom - point[1]) / (bottom - top), config["y_axis"]))


def same_point(a, b):
    return abs(a[0] - b[0]) < 1e-7 and abs(a[1] - b[1]) < 1e-7


def point(value):
    result = (float(value[0]), float(value[1]))
    if not all(math.isfinite(v) for v in result):
        raise ExtractionError("selected stroke contains nonfinite coordinates")
    return result


def bezier_points(p0, p1, p2, p3, tolerance, depth=0):
    """Adaptive flattening; control-polygon excess also preserves collinear turns."""
    chord = math.dist(p0, p3)
    polygon = math.dist(p0, p1) + math.dist(p1, p2) + math.dist(p2, p3)
    if chord:
        distances = [abs((p3[0]-p0[0])*(p[1]-p0[1]) -
                         (p3[1]-p0[1])*(p[0]-p0[0])) / chord for p in (p1, p2)]
    else:
        distances = [math.dist(p0, p1), math.dist(p0, p2)]
    if max(distances) <= tolerance and polygon - chord <= tolerance:
        return [p0, p3]
    if depth >= 24:
        raise ExtractionError("Bezier subdivision limit reached; check curve_tolerance")
    midpoint = lambda a, b: ((a[0]+b[0])/2, (a[1]+b[1])/2)
    a, b, c = midpoint(p0, p1), midpoint(p1, p2), midpoint(p2, p3)
    d, e = midpoint(a, b), midpoint(b, c)
    f = midpoint(d, e)
    return (bezier_points(p0, a, d, f, tolerance, depth+1)[:-1] +
            bezier_points(f, e, c, p3, tolerance, depth+1))


def path_polylines(drawing, tolerance):
    chains = []
    for item in drawing["items"]:
        if item[0] == "l":
            points = [point(item[1]), point(item[2])]
        elif item[0] == "c":
            points = bezier_points(*(point(p) for p in item[1:5]), tolerance)
        else:
            raise ExtractionError(
                f"selected stroke contains unsupported primitive {item[0]!r}; "
                "select curve-only path_indices or use another extraction method")
        if chains and same_point(chains[-1][-1], points[0]):
            chains[-1].extend(points[1:])
        else:
            chains.append(points)
    if drawing.get("closePath"):
        if len(chains) > 1:
            raise ExtractionError("selected closed stroke has ambiguous subpaths; use another extraction method")
        if chains and not same_point(chains[0][0], chains[0][-1]):
            chains[0].append(chains[0][0])
    return chains


def line_interval(a, b, rect):
    """Return the portion of a line inside an axis-aligned rectangle."""
    start, end = 0.0, 1.0
    for origin, delta, low, high in ((a[0], b[0]-a[0], rect[0], rect[2]),
                                   (a[1], b[1]-a[1], rect[1], rect[3])):
        if delta == 0:
            if origin < low or origin > high:
                return None
        else:
            first, last = sorted(((low-origin)/delta, (high-origin)/delta))
            start, end = max(start, first), min(end, last)
            if end < start:
                return None
    return start, end


def clip_polyline(points, rect, exclusions):
    """Clip line segments geometrically; exclusions split paths instead of bridging."""
    result = []
    current = None
    for a, b in zip(points, points[1:]):
        interval = line_interval(a, b, rect)
        intervals = [interval] if interval and interval[1] > interval[0] else []
        for exclusion in exclusions:
            cut = line_interval(a, b, exclusion)
            if cut is None:
                continue
            parts = []
            for low, high in intervals:
                if cut[1] < low or cut[0] > high:
                    parts.append((low, high))
                else:
                    if cut[0] > low:
                        parts.append((low, min(high, cut[0])))
                    if cut[1] < high:
                        parts.append((max(low, cut[1]), high))
            intervals = parts
        if not intervals:
            current = None
            continue
        for low, high in intervals:
            p = (a[0]+low*(b[0]-a[0]), a[1]+low*(b[1]-a[1]))
            q = (a[0]+high*(b[0]-a[0]), a[1]+high*(b[1]-a[1]))
            if same_point(p, q):
                continue
            if current is not None and same_point(current[-1], p):
                current.append(q)
            else:
                current = [p, q]
                result.append(current)
            if high < 1:
                current = None
    return result


def intersect_rects(a, b):
    out = max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])
    return out if out[0] < out[2] and out[1] < out[3] else None


def stroke_style(drawing):
    dash = drawing.get("dashes") or "[] 0"
    numbers = re.findall(r"[-+]?\d*\.?\d+", dash.split("]")[0])
    return "dashed" if any(float(n) != 0 for n in numbers) else "solid"


def matches(drawing, series, index, tolerance):
    color = drawing.get("color")
    return (color is not None and len(color) == 3 and
            drawing.get("stroke_opacity", 1) > 0 and
            all(abs(a-b) <= tolerance for a, b in zip(color, series["color"])) and
            ("path_indices" not in series or index in series["path_indices"]) and
            ("stroke_style" not in series or stroke_style(drawing) == series["stroke_style"]))


def get_drawings(page):
    if page.rotation:
        raise ExtractionError("rotated pages are not supported; normalize the page rotation first")
    return page.get_drawings(extended=True)


def extract(page, config):
    drawings = get_drawings(page)
    page_rect = tuple(page.rect)
    panel = config["panel"]
    if not (page_rect[0] <= panel[0] < panel[2] <= page_rect[2] and
            page_rect[1] <= panel[1] < panel[3] <= page_rect[3]):
        raise ExtractionError("panel lies outside the page")
    result = {s["name"]: [] for s in config["series"]}
    active_clips = {}
    warnings = []
    for index, drawing in enumerate(drawings):
        level = drawing.get("level", 0)
        active_clips = {k: v for k, v in active_clips.items() if k < level}
        if drawing["type"] == "clip":
            items = drawing["items"]
            active_clips[level] = tuple(items[0][1]) if len(items) == 1 and items[0][0] == "re" else None
            continue
        if drawing["type"] == "group":
            continue
        selected = [s for s in config["series"] if matches(drawing, s, index, config["color_tolerance"])]
        if len(selected) > 1:
            raise ExtractionError(f"path {index} matches multiple series; use disjoint path_indices")
        if not selected:
            continue
        series = selected[0]
        bounds = panel
        for clip in active_clips.values():
            if clip is None:
                raise ExtractionError(f"path {index} uses a nonrectangular PDF clip; manual extraction required")
            bounds = intersect_rects(bounds, clip)
            if bounds is None:
                break
        if bounds is None or intersect_rects(tuple(drawing["rect"]), bounds) is None:
            # Horizontal or vertical strokes have zero-area bounds; handle them below.
            rect = drawing["rect"]
            if bounds is None or rect.x1 < bounds[0] or rect.x0 > bounds[2] or rect.y1 < bounds[1] or rect.y0 > bounds[3]:
                continue
        for chain in path_polylines(drawing, config["curve_tolerance"]):
            for points in clip_polyline(chain, bounds, config["exclude_rects"]):
                result[series["name"]].append({"path_index": index,
                                               "stroke_style": stroke_style(drawing), "points": points})
    missing = [name for name, segments in result.items() if not segments]
    if missing:
        raise ExtractionError("no retained strokes for series: " + ", ".join(missing))
    for name, segments in result.items():
        paths = {s["path_index"] for s in segments}
        if len(paths) > 1:
            warnings.append(f"{name}: {len(paths)} source paths retained separately; their union is not a mean curve")
    return result, warnings


def safe_manifest(config, source_bytes, extracted, warnings):
    # Whitelist numerical calibration. Never serialize runtime paths or arbitrary labels.
    calibration = {key: config[key] for key in ("page", "panel", "x_axis", "y_axis",
                    "exclude_rects", "color_tolerance", "curve_tolerance")}
    calibration["series"] = [{k: s[k] for k in ("name", "color", "path_indices", "stroke_style") if k in s}
                             for s in config["series"]]
    calibration_bytes = json.dumps(calibration, sort_keys=True, separators=(",", ":")).encode()
    summaries = {}
    for name, segments in extracted.items():
        values = [data_point(p, config) for segment in segments for p in segment["points"]]
        summaries[name] = {"segment_count": len(segments), "point_count": len(values),
                           "path_indices": sorted({s["path_index"] for s in segments}),
                           "x_range": [min(p[0] for p in values), max(p[0] for p in values)],
                           "y_range": [min(p[1] for p in values), max(p[1] for p in values)]}
    return {"schema_version": 1, "extractor_version": "2.0.0",
            "source_sha256": hashlib.sha256(source_bytes).hexdigest(),
            "calibration_sha256": hashlib.sha256(calibration_bytes).hexdigest(),
            "calibration": calibration, "series": summaries, "warnings": warnings,
            "review_status": "pending_manual_review", "fitting_applied": False,
            "point_interpretation": "PDF stroke vertices; cubic strokes adaptively flattened in PDF coordinates",
            "limitations": ["No raster, marker, occlusion, or scientific-claim verification",
                            "Same-color source paths may represent different physical curves"],
            "rendering": {"dpi": config["dpi"]}, "pymupdf_version": fitz.VersionBind}


def output_paths(prefix, config):
    prefix = str(prefix)
    paths = {s["name"]: Path(f"{prefix}_{s['name']}.csv") for s in config["series"]}
    paths.update({key: Path(f"{prefix}_{key}.{ext}") for key, ext in
                  (("panel", "png"), ("validation", "png"), ("extracted", "png"), ("manifest", "json"))})
    return paths


def write_outputs(page, config, extracted, source_bytes, prefix, warnings):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from PIL import Image, ImageDraw

    paths = output_paths(prefix, config)
    if len(paths) != len(config["series"]) + 4:
        raise ExtractionError("series name conflicts with an output name")
    if any(path.exists() for path in paths.values()):
        raise ExtractionError("output already exists; choose a new output_prefix to preserve the previous extraction")
    for path in paths.values():
        path.parent.mkdir(parents=True, exist_ok=True)
    scale = config["dpi"] / 72
    pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), clip=fitz.Rect(config["panel"]), alpha=False,
                         colorspace=fitz.csRGB)
    panel_image = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    overlay = panel_image.copy()
    draw = ImageDraw.Draw(overlay)
    fig, ax = plt.subplots(figsize=(7, 5))
    colors = {s["name"]: s["color"] for s in config["series"]}
    for name, segments in extracted.items():
        with paths[name].open("w", newline="", encoding="utf-8") as stream:
            writer = csv.writer(stream)
            writer.writerow(["segment_id", "path_index", "stroke_style", "x", "y", "pdf_x", "pdf_y"])
            for number, segment in enumerate(segments, 1):
                values = [data_point(p, config) for p in segment["points"]]
                for (px, py), (x, y) in zip(segment["points"], values):
                    writer.writerow([number, segment["path_index"], segment["stroke_style"],
                                     format(x, ".17g"), format(y, ".17g"), format(px, ".17g"), format(py, ".17g")])
                ax.plot([p[0] for p in values], [p[1] for p in values], color=colors[name], linewidth=1,
                        linestyle="--" if segment["stroke_style"] == "dashed" else "-",
                        label=name if number == 1 else None)
                draw.line([(p[0]*scale-pix.x, p[1]*scale-pix.y) for p in segment["points"]],
                          fill=(255, 0, 200), width=max(1, round(scale*0.6)))
    for axis in ("x", "y"):
        getattr(ax, f"set_{axis}scale")(config[f"{axis}_axis"]["scale"])
        getattr(ax, f"set_{axis}lim")(*config[f"{axis}_axis"]["limits"])
        getattr(ax, f"set_{axis}label")(axis)
    ax.legend()
    ax.set_title("Extracted strokes (separate segments retained)")
    fig.tight_layout()
    fig.savefig(paths["extracted"], dpi=config["dpi"])
    plt.close(fig)
    panel_image.save(paths["panel"])
    overlay.save(paths["validation"])
    manifest = safe_manifest(config, source_bytes, extracted, warnings)
    # Logical output IDs avoid copying the caller's directory names into shared records.
    manifest["outputs"] = {key: {"sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
                           for key, path in paths.items() if key != "manifest"}
    paths["manifest"].write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


def inventory(page):
    rows = []
    for index, d in enumerate(get_drawings(page)):
        if d.get("color") is not None:
            rows.append({"path_index": index, "color": d["color"], "stroke_style": stroke_style(d),
                         "bounds": list(d["rect"]), "primitives": sorted({i[0] for i in d["items"]})})
    return rows


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, help="JSON calibration and series selection")
    parser.add_argument("--pdf", type=Path, help="source PDF override, relative to the current directory")
    parser.add_argument("--page", type=int, help="1-based page, used with --inventory without a config")
    parser.add_argument("--output-prefix", type=Path, help="output override, relative to the current directory")
    parser.add_argument("--inventory", action="store_true", help="list stroke colors and path indices without extraction")
    args = parser.parse_args(argv)
    try:
        config = json.loads(args.config.read_text(encoding="utf-8")) if args.config else {}
        if not isinstance(config, dict):
            raise ExtractionError("configuration must be a JSON object")
        base = args.config.resolve().parent if args.config else Path.cwd()
        source = args.pdf or (base / config["pdf"] if isinstance(config.get("pdf"), str) else None)
        if source is None:
            raise ExtractionError("provide --pdf or a pdf entry in the config")
        page_number = args.page if args.page is not None else config.get("page", 1)
        if isinstance(page_number, bool) or not isinstance(page_number, int) or page_number < 1:
            raise ExtractionError("page must be a positive, 1-based integer")
        source_bytes = source.read_bytes()
        with fitz.open(stream=source_bytes, filetype="pdf") as doc:
            if page_number > len(doc):
                raise ExtractionError("page is outside the document")
            page = doc[page_number-1]
            if args.inventory:
                print(json.dumps(inventory(page), indent=2))
                return 0
            if args.page is not None:
                raise ExtractionError("use page in the config for extraction; --page is inventory-only")
            config = validate_config(config)
            prefix = args.output_prefix or (base / config.get("output_prefix", "extracted"))
            extracted, warnings = extract(page, config)
            manifest = write_outputs(page, config, extracted, source_bytes, prefix, warnings)
            for name, summary in manifest["series"].items():
                print(f"{name}: {summary['segment_count']} segments, {summary['point_count']} points")
            for warning in warnings:
                print(f"WARNING: {warning}", file=sys.stderr)
            print("Extraction complete. Review the source crop, overlay, segments, and numerical ranges before use.")
        return 0
    except (ExtractionError, json.JSONDecodeError) as exc:
        print(f"Extraction stopped: {exc}", file=sys.stderr)
        return 2
    except (OSError, RuntimeError) as exc:
        # Exception messages may contain input locations. Keep CLI output shareable.
        print(f"Extraction stopped: file or PDF operation failed ({type(exc).__name__}). Check inputs and permissions.",
              file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
