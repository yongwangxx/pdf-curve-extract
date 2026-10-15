#!/usr/bin/env python3
"""Export visible raster pixels with calibrated coordinates; never infer hidden curves."""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import sys
import tempfile

import numpy as np
from PIL import Image, ImageDraw

from extract_curves import ExtractionError, axis_config, data_point, finite_number, rectangle


def validate_config(config):
    if not isinstance(config, dict):
        raise ExtractionError("configuration must be a JSON object")
    out = dict(config)
    out["panel"] = rectangle(config.get("panel"), "panel")
    for name in ("x_axis", "y_axis"):
        out[name] = axis_config(config.get(name), name)
    if not isinstance(config.get("exclude_rects", []), list):
        raise ExtractionError("exclude_rects must be a list of rectangles")
    out["exclude_rects"] = [rectangle(r, "exclude_rects") for r in config.get("exclude_rects", [])]
    for key, default in (("color_tolerance", 0.04), ("white_mixture_min", 1.0)):
        out[key] = finite_number(config.get(key, default), key)
        if not 0 <= out[key] <= 1 or (key == "white_mixture_min" and out[key] == 0):
            raise ExtractionError(f"{key} must be between {'0 (exclusive)' if key == 'white_mixture_min' else '0'} and 1")
    out["dpi"] = finite_number(config.get("dpi", 160), "dpi")
    if not 10 <= out["dpi"] <= 1200:
        raise ExtractionError("dpi must lie between 10 and 1200")
    if "output_prefix" in config and (not isinstance(config["output_prefix"], str) or not config["output_prefix"]):
        raise ExtractionError("output_prefix must be a nonempty string")
    if "source_clip" in config:
        clip = config["source_clip"]
        if (not isinstance(clip, (list, tuple)) or len(clip) != 4 or
                any(isinstance(v, bool) or not isinstance(v, int) for v in clip)):
            raise ExtractionError("source_clip must contain four integer image-edge coordinates")
        rectangle(clip, "source_clip")
        out["source_clip"] = list(clip)
    names = set()
    out["series"] = []
    if not isinstance(config.get("series"), list) or not config["series"]:
        raise ExtractionError("series must be a nonempty list")
    for item in config["series"]:
        if not isinstance(item, dict):
            raise ExtractionError("each series must be an object")
        name = item.get("name", "")
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", name):
            raise ExtractionError("series names must use letters, digits, underscores, or hyphens")
        if name in names:
            raise ExtractionError("series names must be unique")
        names.add(name)
        color = item.get("color")
        if not isinstance(color, (list, tuple)) or len(color) != 3:
            raise ExtractionError("color must contain three RGB values")
        color = [finite_number(c, "color") for c in color]
        if min(color) < 0 or max(color) > 1 or min(color) == 1:
            raise ExtractionError("RGB values must lie between 0 and 1; pure white cannot identify a stroke")
        out["series"].append({"name": name, "color": color})
    from extract_curves import calibration_diagnostics
    calibration_diagnostics(out)
    return out


def load_image(source_bytes):
    with Image.open(io.BytesIO(source_bytes)) as image:
        if getattr(image, "n_frames", 1) != 1:
            raise ExtractionError("use a single native image, not an animated or multipage image")
        if image.getexif().get(274, 1) != 1:
            raise ExtractionError("normalize image orientation before calibrating native pixels")
        if "A" in image.getbands() or "transparency" in image.info:
            if image.convert("RGBA").getchannel("A").getextrema() != (255, 255):
                raise ExtractionError("transparent images require an explicitly prepared opaque source")
        # Copy pixels only: source metadata is deliberately not propagated to outputs.
        return Image.fromarray(np.asarray(image.convert("RGB")))


def extract(image, config):
    rgb = np.asarray(image.convert("RGB"), dtype=np.float64) / 255
    height, width = rgb.shape[:2]
    left, top, right, bottom = config["panel"]
    if not (0 <= left < right <= width-1 and 0 <= top < bottom <= height-1):
        raise ExtractionError("panel must lie inside the native image pixel-center bounds")
    clip = config.get("source_clip", [math.floor(left), math.floor(top), math.ceil(right)+1, math.ceil(bottom)+1])
    if not (0 <= clip[0] < clip[2] <= width and 0 <= clip[1] < clip[3] <= height):
        raise ExtractionError("source_clip lies outside the native image")
    if not (clip[0] <= left and clip[1] <= top and clip[2] > right and clip[3] > bottom):
        raise ExtractionError("source_clip must contain the calibrated panel")
    yy, xx = np.indices((height, width))
    in_panel = (xx >= left) & (xx <= right) & (yy >= top) & (yy <= bottom)
    excluded = np.zeros((height, width), dtype=bool)
    for x0, y0, x1, y1 in config["exclude_rects"]:
        excluded |= (xx >= x0) & (xx <= x1) & (yy >= y0) & (yy <= y1)
    candidates, residuals = [], []
    for series in config["series"]:
        color = np.asarray(series["color"])
        if config["white_mixture_min"] == 1:
            difference = np.abs(rgb-color).max(axis=2)
        else:
            pigment = color-1
            alpha = np.clip(((rgb-1)*pigment).sum(axis=2) / (pigment*pigment).sum(),
                            config["white_mixture_min"], 1)
            difference = np.abs(rgb - (1 + alpha[:, :, None]*pigment)).max(axis=2)
        candidates.append(in_panel & (difference <= config["color_tolerance"] + 1e-12))
        residuals.append(difference)
    matches = np.stack(candidates).sum(axis=0)
    ambiguous = matches > 1
    retained = [mask & ~excluded & ~ambiguous for mask in candidates]
    removed = [mask & excluded for mask in candidates]
    summaries = {}
    for index, series in enumerate(config["series"]):
        y, x = np.nonzero(retained[index])
        summaries[series["name"]] = {
            "candidate_pixel_count": int(candidates[index].sum()),
            "retained_pixel_count": len(x),
            "excluded_candidate_pixel_count": int(removed[index].sum()),
            "ambiguous_candidate_pixel_count": int((candidates[index] & ambiguous).sum()),
            "ambiguous_unexcluded_pixel_count": int((candidates[index] & ambiguous & ~excluded).sum()),
            "columns_present": int(len(np.unique(x))),
        }
    warnings = []
    for series in config["series"]:
        white_residual = config["white_mixture_min"] * (1-min(series["color"]))
        if white_residual <= config["color_tolerance"] + 1e-12:
            warnings.append(f"{series['name']}: the selected color tolerance admits pure white; "
                            "background pixels may match. Inspect the mask and color settings.")
    if ambiguous.any():
        warnings.append("Pixels matching multiple series are omitted from every series; no color winner is assigned.")
    if any(mask.any() for mask in removed):
        warnings.append("Exclusion rectangles remove candidate stroke pixels; inspect the exclusion audit before accepting the extraction.")
    for name, summary in summaries.items():
        if not summary["retained_pixel_count"]:
            warnings.append(f"{name}: no pixels retained; inspect candidates, exclusions, and ambiguity masks.")
    return {"masks": retained, "excluded_masks": removed, "ambiguous": ambiguous,
            "residuals": residuals, "series": summaries, "warnings": warnings,
            "source_clip": clip, "image_size": [width, height]}


def output_paths(prefix, config):
    result = {}
    for series in config["series"]:
        name = series["name"]
        for suffix, extension in (("visible_pixels", "csv"), ("mask", "png"), ("excluded_mask", "png")):
            result[f"series:{name}:{suffix}"] = Path(f"{prefix}_{name}_{suffix}.{extension}")
    for name in ("panel", "overlay", "extracted", "comparison", "exclusion_audit", "ambiguous_mask"):
        result[name] = Path(f"{prefix}_{name}.png")
    result["manifest"] = Path(f"{prefix}_manifest.json")
    if len(set(result.values())) != len(result):
        raise ExtractionError("series name conflicts with an output name")
    return result


def safe_manifest(config, source_bytes, result):
    from extract_curves import calibration_diagnostics
    calibration = {key: config[key] for key in ("panel", "x_axis", "y_axis", "exclude_rects",
                                                "series", "color_tolerance", "white_mixture_min")}
    calibration["source_clip"] = result["source_clip"]
    encoded = json.dumps(calibration, sort_keys=True, separators=(",", ":")).encode()
    return {
        "schema_version": 1, "extractor_version": "2.1.0", "mode": "raster_visible_pixels",
        "source_sha256": hashlib.sha256(source_bytes).hexdigest(),
        "calibration_sha256": hashlib.sha256(encoded).hexdigest(),
        "calibration": calibration, "calibration_diagnostics": calibration_diagnostics(config),
        "source_image_size": result["image_size"], "series": result["series"],
        "ambiguous_pixel_count": int(result["ambiguous"].sum()), "warnings": result["warnings"],
        "review_status": "pending_manual_review", "fitting_applied": False,
        "smoothing_applied": False, "interpolation_applied": False, "averaging_applied": False,
        "point_interpretation": "Visible image pixels, not independent measurements or recovered trajectory samples",
        "coordinates": {"origin": "upper-left native pixel center", "pixel_centers": "integer x and y",
                        "panel_and_exclusions": "inclusive pixel-center coordinates",
                        "source_clip": "integer image edges; right and bottom excluded",
                        "masks": "full source image dimensions; white pixels indicate selection"},
        "color_matching": "maximum absolute RGB-channel residual; optional mixture with white; all multi-series candidates omitted",
        "count_interpretation": "Excluded and ambiguous candidate counts can overlap; retained = candidates - excluded - ambiguous_unexcluded",
        "limitations": ["Hidden strokes and same-color identities cannot be recovered",
                        "Color-selected pixels may include annotations or unrelated marks; inspect masks and source",
                        "Stroke thickness, pixel resolution, compression, and calibration limit numerical precision",
                        "No curve centerline, physical sampling, or scientific-claim verification"],
        "rendering": {"dpi": config["dpi"], "source_upscaling": False},
    }


def render_extracted(config, result, path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.colors import ListedColormap
    from matplotlib.patches import Patch
    from extract_curves import axis_limits

    left, top, right, bottom = config["panel"]
    x0, x1 = math.ceil(left), math.floor(right)
    y0, y1 = math.ceil(top), math.floor(bottom)
    x_edges = [data_point((x, bottom), config)[0] for x in np.arange(x0-.5, x1+1.5)]
    y_edges = [data_point((left, y), config)[1] for y in np.arange(y0-.5, y1+1.5)]
    fig, ax = plt.subplots(figsize=(7, 5))
    try:
        handles = []
        for series, mask in zip(config["series"], result["masks"]):
            selection = mask[y0:y1+1, x0:x1+1]
            grid = np.ma.array(np.ones(selection.shape), mask=~selection)
            ax.pcolormesh(x_edges, y_edges, grid, cmap=ListedColormap([series["color"]]),
                          shading="flat", antialiased=False, rasterized=True)
            handles.append(Patch(color=series["color"], label=series["name"]))
        for axis in ("x", "y"):
            getattr(ax, f"set_{axis}scale")(config[f"{axis}_axis"]["scale"])
            getattr(ax, f"set_{axis}lim")(*axis_limits(config, axis))
            getattr(ax, f"set_{axis}label")(axis)
        ax.set_title("Visible pixels (gaps preserved)")
        ax.legend(handles=handles, loc="upper left", bbox_to_anchor=(1.02, 1), fontsize=9)
        fig.tight_layout()
        fig.savefig(path, dpi=config["dpi"])
    finally:
        plt.close(fig)


def write_outputs(image, config, result, source_bytes, prefix):
    from review_images import save_comparison
    paths = output_paths(prefix, config)
    if any(path.exists() for path in paths.values()):
        raise ExtractionError("output already exists; choose a new output_prefix")
    parent = Path(prefix).parent
    parent.mkdir(parents=True, exist_ok=True)
    # Build every output before publishing any, so validation/rendering errors leave no partial set.
    with tempfile.TemporaryDirectory(prefix="raster-build-", dir=parent) as directory:
        staged = {key: Path(directory)/path.name for key, path in paths.items()}
        rgb = np.asarray(image).copy()
        overlay, audit = rgb.copy(), rgb.copy()
        ambiguous = result["ambiguous"]
        audit[ambiguous] = (255, 0, 255)
        for index, series in enumerate(config["series"]):
            name = series["name"]
            mask, excluded = result["masks"][index], result["excluded_masks"][index]
            y, x = np.nonzero(mask)
            with staged[f"series:{name}:visible_pixels"].open("w", newline="", encoding="utf-8") as stream:
                writer = csv.writer(stream)
                writer.writerow(["native_x_px", "native_y_px", "x", "y", "color_residual"])
                for px, py in zip(x, y):
                    dx, dy = data_point((int(px), int(py)), config)
                    writer.writerow([int(px), int(py), format(dx, ".17g"), format(dy, ".17g"),
                                     format(result["residuals"][index][py, px], ".17g")])
            Image.fromarray(mask.astype(np.uint8)*255).save(staged[f"series:{name}:mask"])
            Image.fromarray(excluded.astype(np.uint8)*255).save(staged[f"series:{name}:excluded_mask"])
            overlay[mask] = (255, 0, 200)
            audit[excluded] = (255, 128, 0)
        crop = tuple(result["source_clip"])
        image.crop(crop).save(staged["panel"])
        Image.fromarray(overlay).crop(crop).save(staged["overlay"])
        audit_image = Image.fromarray(audit)
        draw = ImageDraw.Draw(audit_image)
        for rect in config["exclude_rects"]:
            draw.rectangle(rect, outline=(255, 128, 0), width=1)
        audit_image.crop(crop).save(staged["exclusion_audit"])
        Image.fromarray(ambiguous.astype(np.uint8)*255).save(staged["ambiguous_mask"])
        render_extracted(config, result, staged["extracted"])
        save_comparison(staged["panel"], staged["extracted"], staged["comparison"])
        manifest = safe_manifest(config, source_bytes, result)
        manifest["audit_colors"] = {"removed_candidate_pixels": "orange", "ambiguous_candidate_pixels": "magenta",
                                    "both_removed_and_ambiguous": "orange"}
        manifest["outputs"] = {key: {"sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
                               for key, path in staged.items() if key != "manifest"}
        staged["manifest"].write_text(json.dumps(manifest, indent=2)+"\n", encoding="utf-8")
        published = []
        try:
            # Exclusive hardlinks avoid overwriting an extraction created during rendering.
            for key, destination in paths.items():
                os.link(staged[key], destination)
                published.append(destination)
        except OSError:
            for destination in published:
                destination.unlink()
            raise
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True, help="JSON native-pixel calibration and color selection")
    parser.add_argument("--image", type=Path, help="native image override, relative to current directory")
    parser.add_argument("--output-prefix", type=Path, help="output override, relative to current directory")
    args = parser.parse_args(argv)
    try:
        config = validate_config(json.loads(args.config.read_text(encoding="utf-8")))
        base = args.config.resolve().parent
        source = args.image or (base/config["image"] if isinstance(config.get("image"), str) else None)
        if source is None:
            raise ExtractionError("provide --image or an image entry in the config")
        source_bytes = source.read_bytes()
        image = load_image(source_bytes)
        result = extract(image, config)
        prefix = args.output_prefix or base/config.get("output_prefix", "raster")
        manifest = write_outputs(image, config, result, source_bytes, prefix)
        for name, summary in manifest["series"].items():
            print(f"{name}: {summary['retained_pixel_count']} visible pixels; "
                  f"{summary['excluded_candidate_pixel_count']} excluded; "
                  f"{summary['ambiguous_unexcluded_pixel_count']} ambiguous outside exclusions")
        for warning in result["warnings"]:
            print(f"WARNING: {warning}", file=sys.stderr)
        print("Visible-pixel export complete. Review the side-by-side comparison, masks, and exclusion audit before use.")
        return 0
    except (ExtractionError, json.JSONDecodeError) as exc:
        print(f"Extraction stopped: {exc}", file=sys.stderr)
        return 2
    except (OSError, RuntimeError, Image.DecompressionBombError) as exc:
        print(f"Extraction stopped: image or file operation failed ({type(exc).__name__}). Check inputs and permissions.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
