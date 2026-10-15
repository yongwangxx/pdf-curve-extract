# pdf-curve-extract

Extract plotted coordinates from scientific PDF figures and keep the evidence needed to check them. The Python utility reads vector strokes, calibrates linear or logarithmic axes, and exports coordinates with inspection images and a provenance manifest. The accompanying workflow covers source selection, raster digitization, and quantitative review.

Digitized coordinates describe the published graphic. They are not a substitute for the original numerical data, and their accuracy depends on the source resolution, axis calibration, curve separation, and subsequent processing.

## Capabilities

| Task | Support |
| --- | --- |
| Extract line segments and cubic Bézier strokes | Implemented in `extract_curves.py` |
| Calibrate independent linear or logarithmic x and y axes | Implemented |
| Select stroke colors, styles, or individual PDF paths | Implemented |
| Exclude legends and annotations with rectangular masks | Implemented |
| Preserve disconnected segments and PDF path identities | Implemented; no automatic averaging or gap filling |
| Save CSVs, source crop, overlay, extracted-only view, and checksums | Implemented |
| Digitize raster images or recover marker centers | Manual workflow in [SKILL.md](SKILL.md); not implemented by the utility |
| Judge curve identity, uncertainty, and agreement with the paper | Requires review; an extraction run does not certify its results |

The extractor does not reconstruct hidden data, calculate trajectory averages, fit models, smooth curves, or anchor values to numbers reported in the text. It does not interpret error bars or determine which marks belong to a scientific series. Filled-only markers, unusual path types, and complex overlapping plots may require a separate extraction method. Rotated pages and selected strokes under nonrectangular PDF clips are rejected rather than exported with uncertain geometry.

## Install

Download or clone this repository, then install the Python dependencies:

```bash
python -m pip install -r requirements.txt
```

Poppler tools such as `pdftotext`, `pdfimages`, and `pdftoppm` are useful for locating figures and inspecting raster content, but are not required by the vector extractor.

## Try the example

The repository includes a synthetic curve with a same-colored legend sample. Generate its PDF and run the supplied configuration:

```bash
python examples/make_example.py
python extract_curves.py --config examples/figure.json
```

Inspect the files under `outputs/example*`. The configuration excludes the legend and keeps the curve. This example illustrates the workflow without redistributing a published figure; it does not establish accuracy for other source PDFs.

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

File paths inside the configuration are resolved relative to the configuration file. `--pdf` and `--output-prefix` can override those settings. Use `--help` for the available options. Optional settings include `color_tolerance` (default `0.02` per RGB channel), `curve_tolerance` (default `0.05` PDF point), and `dpi` (default `200`). The curve tolerance controls sampling of the PDF geometry; it is not an experimental or digitization uncertainty.

## Inspect the output

For an output prefix of `outputs/figure`, the utility produces:

- `figure_series_a.csv`: coordinates in data units and PDF units, with segment, path, and stroke-style identifiers. Each named series has its own CSV. Preserve segment boundaries when plotting; joining separate segments can invent a curve across a gap.
- `figure_panel.png`: the rendered source crop.
- `figure_validation.png`: extracted geometry over the same source crop.
- `figure_extracted.png`: extracted geometry alone, useful for finding stray legend samples or annotations.
- `figure_manifest.json`: source checksum, extraction settings, output checksums, and extraction diagnostics. It does not contain filesystem locations or certify scientific agreement.

Review the whole overlay, including endpoints, steep regions, peaks, crossings, and gaps. Check several independently chosen coordinates or reported quantities against the paper, with the same units, conditions, series identity, and definition. Agreement at one point does not validate the rest of a curve. Record unresolved regions and the precision supported by the source.

Keep the original extraction separate from any later interpolation, smoothing, fitting, or correction. Every processed result should identify the input checksum, transformation, parameters, excluded points, and effect on the values of interest. A visually plausible fitted curve can conceal an extraction error.

See [SKILL.md](SKILL.md) for the full extraction and review procedure, including raster figures, overlapping trajectories, logarithmic overlays, and uncertainty.

## Sharing results

Cite the source publication and identify the figure, panel, and version. Include the extraction settings, coordinate tables, inspection images where redistribution is permitted, and a short review record. Use checksums and logical asset identifiers to connect these records. Share repository-relative asset references only; keep machine locations, configuration, and location-bearing logs out of published files and history.
