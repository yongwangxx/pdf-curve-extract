# pdf-curve-extract

Extract plotted coordinates from scientific figures and keep the evidence needed to check them. Separate utilities handle PDF vector strokes, explicitly selected PDF markers, and visible colored pixels in raster images. They calibrate linear or logarithmic axes and export coordinates, inspection images, and a provenance manifest.

Digitized coordinates describe the published graphic. They are not a substitute for the original numerical data, and their accuracy depends on the source resolution, axis calibration, curve separation, and subsequent processing.

## Capabilities

| Task | Support |
| --- | --- |
| Extract line segments and cubic Bézier strokes | Implemented in `extract_curves.py` |
| Calibrate independent linear or logarithmic x and y axes | Implemented |
| Calibrate from labeled ticks and check additional ticks | Implemented; residuals are reported for review |
| Select stroke colors, styles, or individual PDF paths | Implemented |
| Exclude legends and annotations with rectangular masks | Implemented |
| Preserve disconnected segments and PDF path identities | Implemented; no automatic averaging or gap filling |
| Save CSVs, source crop, overlay, extracted-only view, and checksums | Implemented; a left/right comparison is also generated |
| Recover square, circle, and cross marker centers | `extract_markers.py`, with explicit path selection and shape checks |
| Preserve vertical error-bar endpoints | Explicit cap-path pairing in `extract_markers.py`; no automatic association or statistical interpretation |
| Extract visible colored raster strokes | `extract_raster.py`; pixel coordinates and masks, not complete hidden trajectories |
| Check whether exclusions remove selected content | Vector exclusion diagnostics; raster removal counts and an exclusion-audit image |
| Judge curve identity, uncertainty, and agreement with the paper | Requires review; an extraction run does not certify its results |

The utilities do not reconstruct hidden data, calculate trajectory averages, fit models, smooth curves, or anchor results to numbers reported in the text. They do not determine which marks belong to a scientific series. Marker extraction requires explicit selection; raster extraction requires reviewed colors and exclusions. Unsupported or ambiguous marker geometry, rotated PDF pages, and selected strokes under nonrectangular PDF clips are rejected.

## Install

Download or clone this repository, then install the Python dependencies:

```bash
python -m pip install -r requirements.txt
```

Poppler tools such as `pdftotext`, `pdfimages`, and `pdftoppm` are useful for locating figures and inspecting raster content, but are not required by the vector extractor.

## Try the examples

The repository includes a synthetic curve with a same-colored legend sample. Generate its PDF and run the supplied configuration:

```bash
python examples/make_example.py
python extract_curves.py --config examples/figure.json
```

Inspect the files under `outputs/example*`. The configuration excludes the legend and keeps the curve. This example illustrates the workflow without redistributing a published figure; it does not establish accuracy for other source PDFs.

Two further synthetic examples exercise marker extraction and raster masks:

```bash
python examples/make_marker_example.py
python extract_markers.py --config examples/markers.json
python examples/make_raster_example.py
python extract_raster.py --config examples/raster.json
```

The comparison image puts the original on the left and the new plot on the right. The overlay is a separate file for checking geometric alignment.

## Extract a vector figure

First inspect the page and its drawing inventory:

```bash
python extract_curves.py --pdf paper.pdf --page 3 --inventory
```

Create a configuration file, for example `figure.json`:

```json
{
  "pdf": "paper.pdf",
  "page": 3,
  "panel": [72, 180, 360, 396],
  "x_axis": {"scale": "linear", "limits": [0, 100]},
  "y_axis": {"scale": "log", "limits": [0.1, 10]},
  "series": [
    {"name": "series_a", "color": [0, 0, 1], "stroke_style": "solid"}
  ],
  "exclude_rects": [[250, 190, 350, 230]],
  "output_prefix": "outputs/figure"
}
```

These coordinates and values are illustrative. Replace them with measurements from the figure; a color match alone cannot distinguish a curve from its legend or annotations. Add `path_indices` to a series when the drawing inventory shows that individual paths must be selected.

```bash
python extract_curves.py --config figure.json
```

Existing output files are not overwritten. For a new run, choose a different `output_prefix` or use `--output-prefix outputs/figure_v2`.

The page number is one-based. The panel and exclusion rectangles use PDF coordinates in points, with x increasing to the right and y increasing downward. The panel is `[left, top, right, bottom]`; x-axis limits give values at the left and right edges, and y-axis limits give values at the bottom and top edges. Logarithmic limits must be positive. Verify both edge values and interior ticks before extraction.

For PDF figures, an optional `source_clip` rectangle can include axis labels and surrounding context in the preview. It uses PDF points, must contain the calibrated `panel`, and must lie inside the page. It changes the source crop and overlay, not the coordinates being extracted. The marker helper accepts the same setting.

### Use labeled ticks for calibration

The plot frame does not necessarily coincide with the first and last labeled ticks. Instead of `limits`, an axis can use two measured `[coordinate, value]` anchors and optional independent check ticks:

```json
{
  "scale": "log",
  "anchors": [[120, 10], [240, 100]],
  "check_ticks": [[180, 31.6227766017]]
}
```

Use either `limits` or `anchors`, not both. Coordinates are PDF points for vector/marker extraction and native pixel coordinates for raster extraction; y coordinates increase downward in both cases. The manifest records check-tick residuals, without declaring an automatic pass or inferring an experimental uncertainty. Verify the selected anchors against the original labels before interpreting those residuals.

File paths inside the configuration are resolved relative to the configuration file. `--pdf` and `--output-prefix` can override those settings. Use `--help` for the available options. Optional settings include `color_tolerance` (default `0.02` per RGB channel), `curve_tolerance` (default `0.05` PDF point), and `dpi` (default `200`). The curve tolerance controls sampling of the PDF geometry; it is not an experimental or digitization uncertainty.

## Inspect the output

For an output prefix of `outputs/figure`, the utility produces:

- `figure_series_a.csv`: coordinates in data units and PDF units, with segment, path, and stroke-style identifiers. Each named series has its own CSV. Preserve segment boundaries when plotting; joining separate segments can invent a curve across a gap.
- `figure_panel.png`: the rendered source crop.
- `figure_validation.png`: extracted geometry over the same source crop.
- `figure_extracted.png`: extracted geometry alone, useful for finding stray legend samples or annotations.
- `figure_comparison.png`: original and new plot side by side. Preview resizing does not alter the coordinate tables or native inspection images.
- `figure_manifest.json`: source checksum, extraction settings, output checksums, and extraction diagnostics. It does not contain filesystem locations or certify scientific agreement.

Review the whole overlay, including endpoints, steep regions, peaks, crossings, and gaps. Check several independently chosen coordinates or reported quantities against the paper, with the same units, conditions, series identity, and definition. Agreement at one point does not validate the rest of a curve. Record unresolved regions and the precision supported by the source.

Keep the original extraction separate from any later interpolation, smoothing, fitting, or correction. Every processed result should identify the input checksum, transformation, parameters, excluded points, and effect on the values of interest. A visually plausible fitted curve can conceal an extraction error.

See [SKILL.md](SKILL.md) for the full extraction and review procedure, including raster figures, overlapping trajectories, logarithmic overlays, and uncertainty.

## Extract selected markers

Use the PDF inventory to identify individual marker paths. The inventory includes filled-only paths as well as strokes. Start from [examples/markers.json](examples/markers.json), set each series' `marker_shape` and `marker_path_indices`, and run:

```bash
python extract_markers.py --config markers.json
```

Supported shapes are squares, circles, and crosses. Each selected path must pass the corresponding geometric checks. Centers are computed in PDF coordinates before conversion to data values, which matters on logarithmic axes. Clipped markers and unsupported shapes are rejected. This is a guided extraction method, not automatic detection of all symbols in a figure.

PDF paths can also be covered by later drawing operations or embedded images. The helper does not detect every such occlusion, so inspect the source crop before accepting a geometrically valid center.

For vertical error bars, provide `lower_cap_path_indices` and `upper_cap_path_indices` in the same order as `marker_path_indices`. Both lists are required together. The helper checks horizontal caps and their alignment with the marker, and exports lower and upper data values without assigning a statistical meaning. The panel must include the full selected geometry; when needed, enlarge it and calibrate with tick anchors.

## Extract visible raster strokes

Start from [examples/raster.json](examples/raster.json) and use the native embedded image where possible:

```bash
python extract_raster.py --config raster.json
```

The `image` is the source raster. `panel` and `exclude_rects` use native pixel-center coordinates, with inclusive boundaries; exported pixel coordinates are integer column and row indices. An optional `source_clip` defines the preview crop with integer image edges and exclusive right/bottom edges. Cropping does not change the calibration coordinates.

Choose each series' RGB `color` in the range 0–1 and review `color_tolerance`. Optional `white_mixture_min` permits matching lighter antialiased pixels along a mixture of the chosen color and white. Pixels matching more than one series are left ambiguous, not assigned to a preferred series. Settings that also admit pure white produce a warning because they can select blank background. Inspect the masks and exclusion-audit image, including every region where a legend or label is removed.

The resulting CSVs contain visible colored pixel coordinates. Several pixels in one column can belong to the thickness or rapid motion of a single stroke; they are not separate simulation measurements. Overlapping trajectories remain incomplete. Do not turn the exported pixels into a continuous curve or a trajectory average without a separate, documented analysis.

## Validation

Run the synthetic regression suite with:

```bash
python -m unittest discover -s tests -v
```

The suite checks known coordinates, logarithmic transformations, disconnected geometry, clipping, ambiguous selections, exclusion losses, and output integrity. [VALIDATION.md](VALIDATION.md) records the real-figure checks and their limits. Passing these checks does not certify a new figure automatically.

## Sharing results

Cite the source publication and identify the figure, panel, and version. Include the extraction settings, coordinate tables, inspection images where redistribution is permitted, and a short review record. Use checksums and logical asset identifiers to connect these records. Share repository-relative asset references only; keep machine locations, configuration, and location-bearing logs out of published files and history.
