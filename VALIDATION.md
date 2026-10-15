# Validation and limits

The tests distinguish geometric recovery from recovery of original scientific data. Synthetic examples provide known coordinates for checking the implementation. Published figures test practical selection and calibration; an independent numerical table provides a stronger check when it is available.

## Real-figure checks

Three source panels were reviewed in October 2026. Source images and private case records are not redistributed in this repository.

| Source panel | What was checked | Result and limit |
| --- | --- | --- |
| Ying et al., Fig. 2(a), [DOI: 10.1016/j.eml.2022.101929](https://doi.org/10.1016/j.eml.2022.101929) | Four stress-strain polylines and 66 separately identified marker centers; linear tick calibration | All curve vertices match native PDF geometry. Marker centers agree with the corresponding vertices within 0.00036 PDF point. This checks the graphic, not original calculation precision. |
| Liang et al., Fig. 4(b), [DOI: 10.1103/PhysRevB.108.184203](https://doi.org/10.1103/PhysRevB.108.184203) | Nine classical and nine quantum-corrected markers with error bars, plus 18 experimental crosses; logarithmic axes | The 18 experimental coordinates agree with the authors' plotting table within 0.0051% in temperature and 0.0030% in thermal conductivity. The simulation points were checked against PDF geometry only. |
| Pan et al., Fig. 8(a), [DOI: 10.1021/acs.jpcc.5c03759](https://doi.org/10.1021/acs.jpcc.5c03759) | Four overlapping colored raster strokes, native-pixel calibration and annotation removal | Visible stroke geometry can be recovered. Occlusion prevents recovery of four complete trajectories or the exact final time point. No fitting or hidden-point reconstruction was used. |

These initial checks used the vector utility together with separate reviewed marker and raster steps. They motivated the reusable helpers in version 2.1.0; they are not a claim of fully automatic extraction across arbitrary figures.

The version 2.1.0 helpers were then run against the same source panels. Tick-anchor extraction retained all 66 vector vertices. The marker helper recovered 102 centers across the two vector figures with no coordinate difference from the earlier reviewed marker extraction, and retained the explicitly paired error-bar endpoints. The raster helper produced masks, an exclusion audit, and a new plot; it conservatively omitted pixels compatible with more than one selected color. Its output remains visible pixel geometry rather than complete trajectories.

Several details affected the results:

- A stress axis extended below zero even though zero was its first labeled tick. Calibrating from the frame as if it were zero would shift the extracted values.
- A source graphic and its accompanying prose gave slightly different peak values. The extracted coordinates retained the graphic rather than being forced to match the prose.
- Marker centers on logarithmic axes must be found in page coordinates before applying the nonlinear mapping.
- A raster annotation mask initially removed a few real curve-tip pixels. An enlarged inspection allowed the mask to be tightened without admitting text.
- Dense overlap concealed parts of individual trajectories. The visible pixel bundle did not support an arithmetic mean or statistical uncertainty estimate.

## What the automated tests establish

The synthetic regression suite exercises linear, logarithmic and reversed calibration; tick-anchor residuals; curve connectivity; explicit marker geometry and cap pairing; native clipping; ambiguous color matches; excluded pixels; and output hashes. Examples generate their own source assets, so the suite does not depend on a paper download or redistribute published figures.

Run it with `python -m unittest discover -s tests -v`. Numerical checks are tied to their fixture and coordinate system. They do not provide a universal extraction-error bound.

## Review a new figure

Inspect the source identity, axes, series assignments, exclusions and final rendering. Compare independently chosen points or reported quantities under the same definitions and conditions. Retain unresolved intervals, and distinguish graphical coordinates from original measurements or simulation output. Generated manifests remain pending manual review until that work is completed.
