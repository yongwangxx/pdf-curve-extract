---
name: pdf-curve-extract
description: Extract and review plotted coordinates from scientific PDF figures using vector paths or a documented raster-digitization workflow, with axis calibration, overlays, quantitative checks, and provenance.
---

# PDF curve extraction and review

Recover the coordinates represented by a published figure at a defensible precision, and retain the evidence needed to evaluate them. Distinguish original numerical data, digitized graphic coordinates, and processed or fitted results throughout the work.

The supplied `extract_curves.py` implements vector-stroke extraction. Raster digitization, marker identification, and scientific review require separate work. See [README.md](README.md) for the command-line interface, configuration, and generated files.

## Select and identify the source

1. Prefer published numerical data when they contain the quantity and series actually plotted. Check the paper's data-availability statement, supplementary files, and cited repository. Simulation input files or trajectories are not necessarily the plotted results; deriving a result from them is a separate calculation.
2. Otherwise inspect the PDF for vector strokes and embedded images. A page can contain both. An image inventory alone does not establish how a particular panel was drawn.
3. For raster content, prefer the original embedded image when it preserves the relevant panel. Rendering at higher resolution may make inspection easier but does not add information to an embedded raster.

Record the publication identifier, version, PDF checksum, one-based page, figure/panel, and scientific series identity. Read the caption and relevant methods or results before choosing what to extract. Confirm the quantity, units, conditions, normalization, and meaning of bands or error bars. Retain a source crop with enough labels to check those choices.

## Calibrate the axes

Work in one documented coordinate system. PyMuPDF page coordinates and image coordinates increase downward in y; another PDF library may use a different origin or orientation. For a raster crop, record its relationship to the source image or page. Inspect rotation and cropping before assigning coordinates.

Use labeled ticks to establish the transformation. Two labeled positions determine an affine calibration; additional interior ticks provide independent checks. Do not assume that the frame endpoints carry the first and last visible tick values. Check reversed axes and multipliers such as `×10³`. Broken axes, non-Cartesian plots, and nonlinear scales other than logarithmic require a separate mapping.

For a pixel or page coordinate `p`, a linear axis follows

```text
v(p) = v0 + (p - p0) / (p1 - p0) * (v1 - v0).
```

For a logarithmic axis with positive endpoint values,

```text
log(v(p)) = log(v0) + (p - p0) / (p1 - p0) * (log(v1) - log(v0)).
```

Apply the logarithm before interpolation and exponentiate afterward. This applies independently to x and y, including the reversed coordinate direction for y. The logarithm base does not change the mapping when used consistently. Record calibration anchors and check residuals at other ticks; a log-like tick pattern alone is insufficient.

## Extract vector geometry

Inspect the drawing inventory and render the panel before selecting paths. Use color, stroke style, geometry, and path identity together. Legends, arrows, and colored text can share a curve's color; exclude or identify them explicitly. Filled outlines often represent text or markers, but stroked paths can also contain annotations.

Preserve the order and connectivity of each subpath. Sample curved segments to a stated geometric tolerance, retaining sufficient points to resolve bends. Endpoint-only sampling of a Bézier curve replaces it with a chord. Conversely, a short path may contain valid data and should not be removed solely because it is short.

Do not pool every same-colored path and sort the result by x. That can merge different series, join gaps, erase loops, or turn a vertical jump into a misleading average. Preserve disconnected segments in the exported table and inspection plots. Any later resampling is a separate processing step.

Markers need their centers identified independently of connecting lines and error bars. If a marker is hidden, leave it unresolved rather than copying a value from another series or a cross-series mean. A shared x grid does not imply a shared y value.

The utility exports stroke geometry, not a determination that every selected path represents data. Inspect both the overlay and extracted-only view before accepting the output.

## Digitize raster figures

This is a manual workflow, not an implemented mode of the vector utility.

- Keep the native image and record the crop, calibration, and image checksum. Inspect the resolution, compression, antialiasing, and line thickness before choosing the reporting precision.
- For color segmentation, choose a color model and tolerance suited to the actual image. Inspect the mask itself. A universal RGB threshold is unreliable near dark text, antialiased edges, or compressed color boundaries.
- Exclude legends, labels, arrows, frame lines, insets, and other decorations before tracing. Save the exclusion regions and diagnostic mask.
- A column-wise center estimate is appropriate only where one identifiable, locally single-valued trace occupies the column. It can fail at steep slopes, loops, crossings, error bars, and thick or noisy bundles. Use connected components or local tracing only where the identity remains supported by visible pixels.
- Separate marker centers and error-bar endpoints. Symmetric error bars are not automatically harmless: clipping, overlap, and marker geometry can bias a pixel median. Do not truncate a trace merely to suppress an unexplained artifact.
- Preserve gaps and ambiguous intervals. Bridging, despiking, or smoothing must be an explicit later operation, with the affected points and parameters retained. Compare before and after; a filter can remove a genuine peak or discontinuity.

For overlapping trajectories, recover individual traces only where their identities can be followed. A center or envelope of the visible pixel bundle is a graphical summary; it is **not** the arithmetic mean or statistical spread of the trajectories. Hidden strokes, different widths, drawing order, and unequal sampling bias such summaries. If a representative center is useful, label it accordingly and record the method and unresolved regions. Do not infer standard deviations, confidence intervals, or missing trajectories from the bundle width.

## Keep extraction and processing separate

Save the initial digitization unchanged. When an error is found, retain it as a superseded result and produce a corrected extraction with the reason and changed records identified. Do not repair a bad endpoint by silently replacing it with a reported plateau or a fitted value.

Fitting, smoothing, interpolation, normalization, and anchoring are optional analytical choices; none is a default extraction step. When used, save a separate output and record:

- the input checksum and retained or excluded points;
- the operation, model or equation, parameters, constraints, and weighting;
- any value imposed from the paper and its source location;
- residuals and changes in the quantities relevant to the intended use.

A reported saturation value need not equal the last point of a finite-time trajectory. An anchored fit can approach that value while concealing a defective input point. Without the fitting implementation or input record, the effect of such a point cannot be established retrospectively.

Keep source-reported uncertainty separate from digitization uncertainty and model-fit uncertainty. A line-width or pixel-based estimate reflects extraction precision, not experimental scatter. Retain the source's stated meaning of a range or `±` value; do not rename it a standard deviation or confidence interval without evidence.

## Review the data and the final figure

An overlay checks geometric agreement, not the correctness of the scientific interpretation. Review both.

1. **Geometry.** Place the extracted coordinates back on the source crop using the inverse of the calibration. Pixel/page coordinates are a reliable comparison space for both linear and log axes. Do not place a uniformly sampled raster into a linear data extent and then change the axes to logarithmic; that distorts its mapping. Inspect the full curve and enlarged views of endpoints, peaks, rapid changes, crossings, gaps, and exclusion boundaries. Use points or segment-preserving lines so that rendering does not hide missing data.
2. **Identity and quantities.** Verify series assignments, units, conditions, and normalization against the paper. Compare independently selected positions across the domain and any consequential reported peaks, plateaus, ratios, integrals, or widths. A numeric match is useful only when it refers to the same quantity and conditions. Record discrepancies rather than forcing agreement.
3. **Precision and limits.** Estimate how calibration, line width, raster resolution, and ambiguous tracing affect the quantities being reported. Vary plausible tick or tracing choices when that uncertainty matters. Do not claim a global error bound from a few spot checks. Set acceptance criteria for the intended scientific use; no fixed number of matching points makes every figure valid.
4. **Final rendering.** Check the actual exported figure, not only an intermediate plot. Confirm its data input, transformations, scales, units, legend, uncertainty labels, and caption. Check for clipped endpoints, unintended interpolation, missing symbols, and resampled raster detail. Reopen vector and raster exports at the intended size. A side-by-side comparison helps assess presentation but does not replace a calibrated overlay.

A review record should state what was inspected, the numerical comparisons and tolerances, unresolved intervals, and the permitted interpretation of the result. Distinguish a quantitatively accepted extraction from an approximate trend reconstruction or a result still needing review. An automatically generated manifest is not a completed review.

## Preserve the evidence

Keep the source identity and checksum, extraction settings, coordinate table, source crop, overlay, extracted-only view or mask, processing records, and review result together under a logical asset or run identifier. Record which exact table produced the final figure, using checksums. Keep numerical precision consistent with the evidence while retaining unrounded working values for reproducibility.

Shared records may use publication identifiers and repository-relative asset references. Do not publish local or remote filesystem locations, machine identities, configuration, location-bearing logs, or mappings from logical identifiers to execution locations. Inspect the exact files being shared, including generated metadata and image annotations. Retain copyrighted source images privately when redistribution is not permitted; a checksum and source citation can still identify the reviewed material.
