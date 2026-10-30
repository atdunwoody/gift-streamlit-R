# GIFT Habitat Explorer: Streamlit

A Python Streamlit application using the original GIFT R calculation functions
for the Upper Grande Ronde stream network. The app includes the input data,
verified R calculations, pinned original R source, and all source needed to run
or modify it. R is required. No ChatGPT account or connection is
needed by the running app.

## Install and run in VSCode

1. Extract the ZIP and open the **gift-streamlit** folder in VSCode. This folder
   contains `app.py`, `run_streamlit.py`, and `requirements.txt`. The local Conda
   configuration is in `dev/environment.yml`.
2. In an Anaconda/Miniconda terminal opened in that folder, run:

   ```bash
   conda env create -f dev/environment.yml
   conda activate gift_streamlit
   ```

   Alternatively, create the combined Python/R environment and install the Python packages:

   ```bash
   conda create -n gift_streamlit --override-channels -c conda-forge python=3.12 pip r-base r-dplyr r-zoo r-jsonlite -y
   conda activate gift_streamlit
   python -m pip install -r requirements.txt
   ```

   Use one environment creation method. For an existing Python environment,
   `python -m pip install -r requirements.txt` installs the Python dependencies.
   Install R separately if it is not provided by that environment.

3. In VSCode, use **Ctrl+Shift+P → Python: Select Interpreter** and select
   **gift_streamlit**.
4. Run **setup_r.R** once in VSCode's R session or RStudio, then run **check_r.py**
   directly in VSCode. The check starts R and executes one real GIFT calculation.
   The small pure-R `birk` dependency is included in the repository; Rtools is
   not needed for that bundled package. Set `RSCRIPT_PATH` in `settings.py` if
   Rscript is not discovered automatically.
5. Open **run_streamlit.py** and choose **Run Python File in Terminal**. The
   launcher uses that interpreter, starts Streamlit, and opens
   **http://localhost:8501**. Stop the terminal with **Ctrl+C**.

The prepared CHaMP monthly overview is included. The app defaults to CHaMP bankfull
width and Castro and Jackson mean bankfull depth. Verified original-R results
are bundled for that scenario and for CHaMP width/depth, including native curves
and full-curve means for the supplied curve sets. The initial network map and
supplied mean-map options reuse these results. Select a stream segment or a
comparison point to calculate that segment's hydraulic and habitat flow-response
curves. A bounded server cache reuses the physical solution across repeat clicks,
life stages, assessment months, and display units. Old `data/hydraulics/*.bin`
files are not needed by the app and are omitted from this package.
Run the launcher rather than running `app.py` as a plain Python script. If needed, the
equivalent terminal command is `python -m streamlit run app.py`.

### Updating an existing installation

Stop the running server, replace the project files with this version, and run
`python -m pip install -r requirements.txt` in the environment used by VSCode.
Review the paths and field mappings in the new `settings.py`, then run
`setup_r.R`, `check_r.py`, and `run_streamlit.py`. The pinned Streamlit version is required so a
comparison-plot click can refresh the map, inspector, and plots together without
rebuilding the sidebar. Hidden plots and result tables load when opened.
The existing environment can be named `gift_webapp` or `gift_streamlit`;
VSCode and the installation command must use the same environment.

For the deployment-fixes update, remove the old root `environment.yml` after
copying in the changed files. Its replacement is `dev/environment.yml`. Leaving
the old file at the repository root makes Community Cloud use Conda instead of
the pinned `requirements.txt`.

The Dockerfile installs R and the required packages. Streamlit Community Cloud
uses `packages.txt` for R, dplyr, zoo, and jsonlite; the worker installs the bundled
pure-R birk package into its own user cache if needed. The running app does not
download GIFT from GitHub. See `R_BACKEND.md` for source provenance, dependencies,
worker behavior, and rebuilding the supplied R results.

### Map selection performance

- Reuse the network's prepared map records within the session. A different
  reach selection changes the gold highlight and reach results. Records are
  rebuilt when the data, curves, assessment period, network layer, or metric changes.
- Send compact PyDeck JSON with the same coordinate precision. No additional
  geometry simplification is applied for this optimization.
- Build only the visible WUA/HSI and comparison plots. Each plot panel has its
  own fragment, so switching plot tabs does not redraw the map.
- Cache the network comparison points independently of the selected marker.
  Reuse the selected reach's cached physical solution when evaluating habitat.
- Cache the network export until its overview or assessment settings change;
  load the monthly-results table and its downloads when that expander is opened.
- Use 5-pixel selectable stream lines with rounded ends and a 6-pixel gold
  selection. This replaces 3-pixel lines and an ineffective `pickingRadius`
  entry in the WebGL parameters.
- Index the supported reach/month pairs once when constructing period summaries,
  instead of repeatedly scanning the network. Scientific values, period coverage,
  and the ordering of months across December are preserved.

Reach clicks still require a round trip to the Streamlit server. The first
selection solves that reach's native hydraulics. New suitability curves reuse
the cached hydraulics and evaluate habitat; repeat selections reuse both caches.

## Features

- Independent network bankfull width and mean-depth regressions above the map: CHaMP Regression,
  Bieger 2015 National, Bieger 2015 Regional, and Castro and Jackson 2001.
  Defaults are CHaMP width and Castro and Jackson mean depth. HSI/WUA metric
  buttons sit beside these selectors, directly above the map.
  Selections apply to all segments, the inspector, comparison plots, and exports.
- Per-segment width, mean depth, slope, and D84 overrides apply only to response
  plots, the channel cross section, and selected-segment downloads. Choices
  persist by segment; Restore segment defaults restores the active network geometry.
- Segment values appear in editable numeric text boxes without override switches.
  An optional maximum bankfull depth starts blank. Blank preserves the CHaMP
  source maximum for unchanged CHaMP dimensions; alternative or edited width/depth
  uses GIFT width/mean depth/100 for shape. Entered maximum depth overrides this
  behavior for the selected plot and must be at least the mean bankfull depth.
  See `HYDRAULIC_OPTIONS.md` for equations, units, scope, and averaging.
- A Map mean HSI / Map mean WUA on/off switch, off by default. Its label and mapped
  values follow the selected habitat metric. The default map uses median habitat
  at selected monthly flows. Mean mode averages each segment's entire modeled
  flow-response curve over discharge using 201 equally spaced flow samples.
  It is not a temporal or flow-duration-weighted average. Monthly sidebar controls
  are disabled in mean mode; segment monthly values, plot markers, and flow-range
  shading are hidden. A gold diamond shows the full-curve mean at the modeled
  discharge-range midpoint. Switching off restores monthly-flow displays.
- Clickable stream segments with raw mean depth, mean velocity, predicted D84,
  component suitability, HSI, WUA, temperature score, and priority score.
- Select reaches on the map or by clicking a comparison-plot point. Plot
  selections also zoom to the selected reach. Icon controls on the map fit the network or zoom
  to the selected segment; the former COMID search selector has been removed.
- Light, dark, and satellite basemaps. Basemap services require internet access;
  their provider availability is separate from the habitat calculations.
- Single months and inclusive month ranges, including ranges across December.
- Species/life-stage defaults and a reset button.
- WUA, HSI, and Channel Geometry tabs below the map, with depth/velocity suitability
  curves on the secondary axis. Markers show selected monthly results in range
  mode or the full-curve average in mean mode.
- Network layers: GIFT habitat, Temperature Suitability, and Provisional Priority.
  Temperature colors place tan at 80% of the observed maximum, with matching legend stops.
- Five selectable scatterplots: habitat versus temperature suitability, Provisional Model
  priority, depth suitability, velocity suitability, and substrate suitability.
  The Plot WUA on/off button above the graphs selects HSI (off) or WUA (on),
  independently of the map metric. The y axis starts at
  zero and extends 5% above the highest plotted value. Zero-only and empty plots
  use a nonzero fallback range.
- The map offers HSI and raw WUA. The normalized WUA/width ratio is removed
  from controls and CSV exports. Both metrics are available in mean-map mode.
- Chinook - Spawning uses only the supplied stream/river spawning curves.
  The large-river spawning curve set is not offered. The general curve set is
  labeled General salmonid.
- Unavailable-result diagnostics show the prepared GIFT geometry, shape factor,
  modeled flow bounds, curve ranges, monthly inputs, and availability reasons.
  A JSON download retains full input values and the active curves in SI units.
  Diagnostics start collapsed when a selected flow exceeds bankfull.
- Three-file custom suitability uploads, a sidebar View Suitability Curves popup,
  and downloadable CSV examples. Uploads are session-specific. Restarting the
  server clears them. GIFT Shiny Default now uses the user-supplied graph-derived
  CSVs unchanged. Source filenames and checksums are documented under
  `inputs/curves/SHINY_DEFAULT_SOURCE.md`.
- Downloads of the network period summary, selected segment's exact monthly
  results, and its native flow-response curve. CSV exports retain SI units
  regardless of the displayed flow units.

## Meaning of the selected months

Each segment uses its own EROM flow for each selected month. GIFT is evaluated
**separately at every monthly flow**, then the median of monthly HSI and WUA is
reported. This is not habitat evaluated at median discharge. The map's period
summary and the inspector's single-month raw values are kept separate.

**Require every selected month** is checked by default. If any selected month is
unsupported, that segment's seasonal habitat stays unavailable. Unchecking it
permits the median of available months and marks partial coverage. Downloads
include selected and valid month counts and the valid-month list.

Positive flows outside the modeled range are never extrapolated or replaced by
zero. A modeled zero-flow month contributes zero HSI and WUA, with undefined
depth/velocity suitability. Missing hydraulic predictors remain unavailable.

| Species / life stage | Default months | Basis |
| --- | --- | --- |
| Chinook juvenile rearing | July–September | Late-summer assessment; juveniles also rear in other months |
| Steelhead juvenile rearing | July–September | Late-summer assessment; rearing spans multiple seasons |
| Chinook - Spawning (stream/river curves) | August–September | Upper Grande Ronde spring/summer stocks |
| Steelhead spawning | March–June | Basin spawning window; monthly means approximate partial-month endpoints |
| General salmonid | July–September | Assessment period, not calibrated life-stage timing |

These are editable study defaults, not universal occupancy windows. Their sources
and qualifications are retained in `settings.py` and the app's sidebar. Custom
curves initially inherit the previous selected life stage's assessment period;
set the months appropriate to the uploaded curves.

## Input sources and model

- **Streams:** the file and layer selected by `NETWORK_PATH` and `NETWORK_LAYER`
  in `settings.py`. The supplied default is
  `inputs/UGR_channel_geometry_predictions_monthly_EROM.gpkg`, layer
  `channel_geometry_predictions`. Geometry, bankfull width, mean depth, slope,
  D84, and monthly flows come from this file.
- **Maximum bankfull depth:** the configured field in `MAX_DEPTH_PATH` /
  `MAX_DEPTH_LAYER`, joined by the configured ID fields. By default this is
  `bf_depth_max_pred_m` in the same streams GeoPackage.
- **Temperature and priority:** `currTempScoreKSee`, `High priority combined`,
  and `FinalScore_cat` are joined by `COMID` from the `provisional_model_layer` layer of
  `inputs/Provisional_Model_Layer.gpkg`, the supplied Provisional Model Layer
  GeoPackage. All 728 stream COMIDs match, with no null temperature or priority
  scores. The original CSV remains available but is not the active score source.
- COMID 23429096 has missing bankfull width, mean depth, and maximum depth.
  It remains visible with its temperature and priority scores.
- EROM flows are gage-adjusted **1971–2000 monthly climatology**, not current
  observations. Represented alterations and gage corrections are included;
  diversions are incompletely accounted for.
- For CHaMP geometry, shape factor is `1 - mean bankfull depth / joined maximum depth`, without
  capping. All 166 factors above 0.7 in the current inputs are retained in the
  overview and selected-segment calculations. Factors must be in [0, 1).
- Original GIFT equations and 121-bin distribution calculations execute from
  the pinned R functions in `r_gift/upstream`. Suitability uses nearest curve coordinates, not
  linear interpolation of suitability. Hydraulics are interpolated to exact
  monthly flow before suitability is calculated.
- Substrate uses the class containing D84, inclusive lower/exclusive upper
  bounds. D84 represents one grain size, not a full grain-size distribution.
- HSI = depth suitability × velocity suitability × substrate suitability.
  WUA = HSI × wetted width, in m²/m.
- `currTempScoreKSee` is displayed unchanged, using the supplied juvenile-salmon
  interpretation of [See et al. (2021)](https://doi.org/10.1002/ecs2.3404).
  The transformation was not independently reconstructed, and this is not a
  spawning temperature model. Temperature and priority do not change with
  selected months or curves and are not GIFT components.
- Display geometry is simplified by 8 m, independent of hydraulic calculations.
- Map color scales end at the maximum observed value for the active map and
  selected period. The note below each map distinguishes its potential maximum:
  HSI and temperature suitability are bounded by 1; WUA depends on wetted width;
  the supplied priority field has no documented theoretical maximum.
- Flow displays default to cfs. Calculations and explicitly labeled SI downloads
  remain in m³/s. A pale band marks the selected flow envelope on each response
  plot; a dotted line marks a single flow. Unsupported known flows still define
  the envelope, even when habitat cannot be calculated there.
- Hovering over a selectable stream or comparison point shows a pointer cursor.
  Other areas retain the normal map or plot navigation cursor.

## Custom suitability CSVs

Upload one life-stage set consisting of three CSV files:

| File | Required columns | Units |
| --- | --- | --- |
| Depth | `depth,suit` | m; suitability 0–1 |
| Velocity | `velocity,suit` | m/s; suitability 0–1 |
| Substrate | `lower,upper,suit` | mm, mm; suitability 0–1 |

Provide at least two distinct depth and velocity coordinates. Duplicate
coordinates, non-finite values, negative coordinates, suitability outside 0–1,
and overlapping substrate classes are rejected. Substrate gaps are allowed;
segments whose D84 falls in a gap have unavailable HSI/WUA while their raw
hydraulics and depth/velocity component suitability remain available.
Each file is limited to 5 MB and 10,000 data rows. Filter multi-species CSVs to
the intended species/life stage before uploading. Download example CSVs from
the upload expander to see the format.

## Modify the data or defaults

Edit **settings.py** to select the files, layers, fields, and source units.
All preparation entry points use these settings; there is no separate set of
hard-coded paths or field names in the preparation script.

| Setting | Purpose |
| --- | --- |
| `NETWORK_PATH`, `NETWORK_LAYER` | Streams GeoPackage and layer |
| `NETWORK_FIELDS` | Stream ID, name, bankfull width, mean depth, slope, D84, drainage area, summer width, and extrapolation flags |
| `MAX_DEPTH_PATH`, `MAX_DEPTH_LAYER`, `MAX_DEPTH_ID_FIELD`, `MAX_DEPTH_FIELD` | Maximum bankfull depth source and ID join |
| `PRIORITY_PATH`, `PRIORITY_LAYER`, `PRIORITY_ID_FIELD`, `PRIORITY_FIELDS` | Priority score and category source, ID join, and fields |
| `TEMPERATURE_SOURCE`, `TEMPERATURE_FIELD` | Temperature column in the `network` or `priority` source |
| `MONTHLY_FLOW_FIELDS` | Explicit month-to-column mapping for all 12 monthly discharges |
| `FIELD_UNITS` | Source units, converted to SI before GIFT calculations |

For example, to select another width column containing feet:

```python
NETWORK_FIELDS["bankfull_width"] = "my_bankfull_width_ft"
FIELD_UNITS["bankfull_width"] = "ft"
```

Width and depth accept `m` or `ft`; D84 accepts `mm`, `m`, or `in`; slope
accepts `m/m`, `ft/ft`, or `percent`; monthly discharge accepts `m3/s` or `cfs`.
Drainage area accepts `km2` or `mi2`. A source field's units must match its
configured units. Set optional display fields to `None` to disable them.
Required columns must exist, while null row values are retained as unavailable.

After edits, rerun or restart the app. Full reruns automatically check input
files, settings, and field mappings; there is no separate reload button.
Every full app rerun reads current settings and checks the source paths, layers,
field mappings, units, file contents, curve CSVs, and calculation code against
`data/build-info.json`. When inputs change, the app refreshes the stream attributes
without solving hydraulics for every reach. Existing monthly values are reused
only where the physical inputs and monthly flow still match the verified overview.
Changed reaches are gray until selected; selecting one calculates its results
and updates the overview and network export for the current session. If the
overview is absent or corrupt, the app can still display the input network and
calculate reaches on selection. It does not write rebuilt data during startup.
An invalid configuration stops the app with a useful error instead of showing
results from an old source.

Input hashes are recorded by source role and repository-relative path for bundled
inputs (or absolute path for external files). Each source is additionally checked
by SHA-256, so similarly named but different sources cannot silently reuse a
prepared dataset. The app's
**Input sources and field mappings** panel and exported diagnostics report the
actual selected files and columns. **Model input values** shows the selected
reach's bankfull width, mean/maximum depth, slope, and D84 in SI units.

To refresh the complete monthly overview before publishing changed inputs,
run **prepare_r_scenarios.py** directly in VSCode. This refreshes both bundled
scenarios and mean summaries with R. **prepare_data.py** remains an optional
source-overview-only workflow and creates legacy `.bin` files the webapp does
not need.
Running `tools/prepare_network.py` also delegates to the same settings.

During interaction, Streamlit reads verified R results or calls the original R
functions through `r_engine.py`. `habitat_engine.py` holds application summaries
and exports; `presentation.py`
builds the PyDeck map and Plotly figures; `app.py` manages controls and session
state; `runtime_data.py` loads the overview or refreshes inputs without bulk
hydraulic calculations. `prepare_data.py` manages explicit offline rebuilds.

Keep the `static/` folder and `.streamlit/config.toml`: they enable the app's
small satellite basemap style file. Selected hydraulic curves are calculated
in memory and are not served through this static-file route.

## Publish a shareable site

This is a server-based Streamlit app. Use a Streamlit-compatible host, such as
**Streamlit Community Cloud**, rather than uploading its files as a static site.
The previous Vercel static deployment configuration is not used.

1. Add the **contents of gift-streamlit** to a GitHub repository you control.
   Keep `app.py`, `requirements.txt`, `packages.txt`, `setup_r.R`, `.streamlit/config.toml`, `static/`, `data/`,
   `inputs/`, `r_gift/`, `model/`, and all supporting Python modules. The `inputs/` folder
   is required for startup source verification. Keep `tests/` for verification.
   Leave the local Conda file in `dev/`; there must be no root `environment.yml`.
2. Sign in to [Streamlit Community Cloud](https://share.streamlit.io).
3. Choose **Create app**, select the repository and branch, and set the
   entrypoint to **app.py**. If the project is nested in another repository,
   use its corresponding relative path.
4. In Advanced settings, use **Python 3.12**, then deploy. No application
   secrets or map API key are required for the supplied basemaps.
5. Choose an available subdomain and share the resulting **streamlit.app** URL.
   Review the app's sharing settings for the intended viewers.

Community Cloud's deployment procedure is documented at
[Deploy your app](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/deploy).
A public hosted URL has not been created by this conversion. The local launcher
only serves your computer; keep it running while viewing the local app.

For a container host, the included Dockerfile listens on `0.0.0.0` and uses the
host's `PORT` environment variable, defaulting to 8501 when it is unset. Configure
the host's target port to match. Such hosting can support a custom domain and
dedicated resources.

## Performance and validation

The bundled `data/network.json` and `data/curves.json` provide the monthly map
overview. Startup verifies these small overview files and the configured sources;
it does not read, validate, or rebuild the 727 native hydraulic files. No segment
is selected automatically. Selecting a segment computes its 981 native hydraulic
levels, then evaluates its monthly results and habitat response.

Verified R calculations are supplied for CHaMP width/depth and CHaMP width with
Castro and Jackson depth, including full-flow means for the supplied curves.
Other regressions and custom curves execute the original R functions and use
bounded caches. A full uncached network calculation can take several minutes;
use `prepare_r_scenarios.py` before publishing source changes. Earlier timings
for the translated Python model do not describe this R backend.

Selected physical solutions use a bounded 256-entry cache keyed by segment, hydraulic
inputs, and calculation code. Habitat response curves and selected monthly
results have their own bounded caches. Changing a life stage, assessment period,
or display units reuses the physical solution. Changing physical inputs invalidates
that segment's solution. Caches are cleared when the server restarts and can
evict old entries when their limits are reached.

Moving the repository does not invalidate the prepared overview. The manifest
is portable across Windows and Linux.
Text assets use LF line endings, enforced by `.gitattributes`, so a Git checkout
does not invalidate the prepared data through line-ending conversion alone.
When changing inputs or model preparation code, run `prepare_r_scenarios.py` in VSCode
and commit the updated overview files before deploying. The app does not require
the offline-generated `data/hydraulics/` folder.

The app caches the dataset, full-network monthly habitat by curve set, and
native response curves for recently selected segments. The main map/inspector/
plot workspace is a Streamlit fragment, so its interactions can rerun without
rebuilding the sidebar. Panning and zooming use the browser's WebGL map.

In an earlier map-performance revision, six selection events replayed through
a running local Streamlit server showed that
the median fragment-update time decreased from **970.5 ms to 460.2 ms** in the
same development environment. Each run selected three reaches for the first
time and then selected the same three again. The map JSON decreased from
**1,511,126 to 490,305 bytes**, with identical coordinate precision. Only the
two visible plots are built on each selection, compared with four previously.
These timings include the local server and WebSocket response but exclude
browser/GPU rendering, basemap tile requests, internet latency, and concurrent
users. They are not a guarantee of hosted response time. The first selection
with a new curve evaluates that reach's habitat response using cached hydraulics.

Tests use frozen results generated by the original R GIFT functions, including
19 hydraulic cases, factors above 0.7, 1,728 habitat evaluations across all six
curve sets, and 792 exact monthly evaluations. The R source, provenance, input
hashes, and optional regeneration script are included in `tests/r_reference`.
The full verification suite now requires R. It executes the live R functions
as well as the independent Python reference checks, and verifies the prepared
R scenarios against live calculations. Cross-language comparisons retain the
documented numerical tolerances.

The suite also checks all 728 input joins, missing and zero flows, partial periods,
year-crossing ranges, custom uploads, map selection parsing, map serialization,
source changes, alternate field mappings, unit conversions, observed map scales, flow highlighting, Streamlit state
transitions, comparison selections, and diagnostic inputs for unavailable results.

To run development checks, install `pytest` in this environment and run
**run_checks.py** in VSCode. The equivalent terminal command is
`python run_checks.py`. This checks the bundled overview without native files,
then runs the scientific, app-state, and on-demand calculation tests. Run it from a fresh checkout when
validating a release. Tested library versions and verification limits are
recorded in `VALIDATION.md`. Production and local package versions are pinned in
`requirements.txt` and `dev/environment.yml`; transitive dependencies are still
resolved by pip.

## License

The original GIFT Python implementation and this application retain GPL-3.0.
See **LICENSE.txt** and **NOTICE.md**. Preserve the license and make the applicable
source available when distributing the application.
