# Network and selected-segment hydraulic options

Use the **bankfull regression controls above the map** to choose independent width
and mean-depth regressions for every segment. The defaults are CHaMP Regression
for width and Castro and Jackson 2001 for mean depth. The map, inspector, comparison plots, and network export
use these network choices. Bieger options are labeled **Bieger 2015 National**
and **Bieger 2015 Regional**.

## Segment-only overrides

Select a segment, then open **Adjust selected-segment hydraulics**. The
editable numeric boxes start with the segment's current bankfull width (m),
mean bankfull depth (m), slope (m/m), and D84 (mm). Editing a value changes the
plot directly. Unedited fields follow the current network scenario.
Custom values update only the response curves, Channel Geometry tab, monthly
plot table, and segment CSV downloads. The map and network comparisons retain
the above-map regressions. D84 affects both hydraulic resistance and the substrate
suitability class; it is one representative grain size, not a grain-size distribution.

Overrides persist by COMID in the current session, including when network
regressions or species change. **Restore segment defaults** restores the active network
scenario for that segment. Changing the underlying source data clears overrides.
Invalid geometry remains unavailable with an explanation and can be corrected
through the custom inputs; the app does not reuse the old scenario's scores.

## Equations

| Source | Bankfull width | Mean bankfull depth | Input/output units |
| --- | --- | --- | --- |
| Bieger 2015 National | `2.70 * DA**0.352` | `0.30 * DA**0.213` | DA in km2; geometry in m |
| Bieger 2015 Regional, IMP | `1.11 * DA**0.415` | `0.07 * DA**0.329` | DA in km2; geometry in m |
| Castro and Jackson 2001, Western Cordillera | `9.40 * DA**0.42` | `0.61 * DA**0.33` | DA in mi2; geometry in ft, converted to m |

Bieger uses the printed Table 3 coefficients. `BIEGER_REGION` in `settings.py`
selects the division for the Regional option; the supplied basin uses `IMP`,
Intermontane Plateau. All eight divisions and their Table 2 drainage-area ranges
are included. Domain-extrapolation notes appear with the selected segment.
Castro and Jackson uses USGS NSS region 847 (GC1857), Western Cordillera;
its published drainage-area range is 19.6 to 5,090 mi2. The official response
at 100 mi2 returns 65.0 ft width and 2.79 ft depth and is retained under
`tests/regression_reference/`. The running app calls no online regression service.

## Maximum depth and the Channel Geometry tab

The selected-segment **Maximum bankfull depth for plotting (m)** field starts
blank. With a blank field, unchanged CHaMP width/depth uses the source maximum.
Slope-only or D84-only edits preserve that maximum. Alternative regressions or
edited width/depth omit maximum depth, and GIFT uses
`shape_factor = (bankfull_width / mean_bankfull_depth) / 100`.

An entered maximum depth overrides automatic behavior for that plot, regardless
of the network regression. It must be positive and at least the mean depth.
**Restore segment defaults** clears it and restores width, depth, slope, and D84.
Factors are not capped; values outside [0, 1) remain unavailable.

The **Channel Geometry** tab plots the exact 10,001-point cross-section grid used
by the hydraulic simulator, with the bankfull water surface at elevation zero.
It follows the active segment plot inputs. Axes use metres; vertical exaggeration
is stated. This is GIFT's assumed section, not a surveyed section. Maximum depth
shown there is the geometry resulting from the automatic behavior or the explicit plot input.

## Selected-flow map versus mean-habitat map

The mean-map switch defaults off. Its label is **Map mean HSI** or **Map mean WUA**,
following the metric buttons beside the regression selectors. By default, habitat is
calculated at each exact monthly EROM flow, then the median of selected months
is mapped. Complete/partial-period rules still apply.

With the switch on, each segment's mean HSI or WUA over its full modeled flow curve is
mapped. This is a separate mean for each segment, not one basin-wide number.
The calculation uses 201 equally spaced discharges from the native modeled
minimum to bankfull, linearly interpolates hydraulic variables, evaluates the
existing GIFT habitat equations at each discharge, and divides the trapezoidal
integral of the selected metric by the modeled discharge range. It does not extrapolate to zero
or above bankfull. This is an average over discharge, not a time mean and not
weighted by flow duration. Mean component suitability is reported separately;
mean HSI is not obtained by multiplying mean components.

Mean mode drives the habitat map, inspector, comparison plots, and network
summary export. The month/range controls are disabled, and the inspector shows
full-curve means instead of monthly flow information. HSI/WUA plots omit monthly
markers, the inspected-month highlight, and selected-flow shading. A gold diamond
shows the curve mean, placed at the modeled discharge-range midpoint. Its height
is the integral mean, not necessarily the curve's value at that discharge.
Local plot edits update this point while network means stay unchanged.
Switching off restores monthly controls and results. The HSI/WUA buttons remain
available in mean mode. The independent **Plot WUA** switch above the comparisons
selects HSI (off) or WUA (on) for all five comparison plots, including substrate
suitability. Temperature and provisional priority values remain unchanged.

## Curve viewer and the Shiny preset

**View Suitability Curves** in the sidebar opens a dialog with the active depth,
velocity, and substrate curves. It works for the supplied life stages and custom
uploads. Substrate plots show each class interval explicitly.

**GIFT Shiny Default** uses the three graph-derived CSVs supplied by the user
on 2026-10-26. All CSV values are copied unchanged. Depth and velocity each have
351 rows; substrate has 72 classes. The app uses the existing nearest-coordinate
and class-lookup rules, without smoothing, rescaling, or adding endpoints.
See `inputs/curves/SHINY_DEFAULT_SOURCE.md` and the frozen SHA-256 reference.

## Performance and exports

CHaMP width/depth and the default CHaMP-width/Castro-and-Jackson-depth scenario
use verified original-R calculations bundled with the project. Other network
regression combinations execute the original R functions and are cached.
Two complete physical scenarios are cached independently of suitability curves;
monthly and full-flow habitat summaries have their own bounded caches. Selecting
a segment and changing plot overrides use the existing per-segment caches.
R, dplyr, zoo, birk, and jsonlite are now required. No running-app connection to
GitHub is needed. See `R_BACKEND.md` for setup and offline preparation.

Network exports record active width/depth sources and explicitly distinguish
selected-month medians from full-flow means. Full-flow coverage is counted in
flow samples; monthly coverage remains counted in months. Segment CSVs record
active sources, width, mean depth, slope, D84, shape factor, and optional maximum
depth. Maximum-depth provenance identifies explicit plot values versus the
automatic CHaMP or GIFT behavior. Exports identify the R engine and pinned source
revision. Original source GeoPackages are unchanged; the prepared CHaMP
overview has been recalculated with the original R functions.

## References

Bieger K., Rathjens H., Allen P.M., and Arnold J.G. (2015). Development and
Evaluation of Bankfull Hydraulic Geometry Relationships for the Physiographic
Regions of the United States. JAWRA 51:842-858. Table 3.
https://doi.org/10.1111/jawr.12282

Castro J.M. and Jackson P.L. (2001). Bankfull Discharge Recurrence Intervals and
Regional Hydraulic Geometry Relationships: Patterns in the Pacific Northwest,
USA. JAWRA 37:1249-1262.
https://doi.org/10.1111/j.1752-1688.2001.tb03636.x

USGS NSS scenario definition:
https://streamstats.usgs.gov/nssservices/scenarios?regressionregions=847&statisticgroups=24

USGS estimates were obtained from the corresponding `scenarios/estimate`
service with drainage area set to 100 mi2 on 25 October 2026.
