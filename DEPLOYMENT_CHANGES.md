# Original R GIFT integration, 2026-10-30

The Streamlit app now executes the pinned original R `AvgHydraulics()` and
`Habitat()` functions through a persistent Rscript worker. Native stages and
the cross-section grid are exposed by changing only the return expression of
an in-memory function copy. The source files remain unchanged. All existing
controls, geometry defaults, D84 treatment, and aggregation methods are retained.

R is now required. Use `setup_r.R`, `check_r.py`, and `run_streamlit.py` for local
setup and launch. `packages.txt` and the Dockerfile include deployment
dependencies. The bundled pure-R birk package can install without a download.
There is no Python calculation fallback. Missing R dependencies display an
explicit setup error.

Original-R calculations are bundled for both default network scenarios and
supplied mean-map curves. Run `prepare_r_scenarios.py` after input changes.
Source hashes and the GIFT revision are checked before reuse. CSVs and input
notes identify the calculation engine and source. See `R_BACKEND.md`.

The dated notes below describe previous releases and their earlier defaults.

# Habitat controls and spawning curves, 2026-10-30

The app now offers one Chinook spawning option, **Chinook - Spawning**, using
the supplied stream/river curves. **General salmonid** replaces the sensitivity
scenario label. Existing large-river Chinook selections move to the stream/river
option when the app reruns.

The default geometry is CHaMP bankfull width with Castro and Jackson mean
bankfull depth. Its first load calculates and caches that network scenario.
HSI/WUA metric buttons sit beside the regression selectors directly above the
map. Mean-map mode supports either metric and labels its switch accordingly.
The comparison panel has its own **Plot WUA** switch, off for HSI and on for WUA,
and includes a new substrate-suitability comparison. The five comparisons retain
click-to-select behavior. See `README.md` and `HYDRAULIC_OPTIONS.md` for details.

The prepared CHaMP overview retains its original hydraulic values. Its active
curve definitions and provenance were updated. No dependencies or hydraulic
equations changed. The older startup notes below describe earlier defaults;
zero startup solves now applies when CHaMP is selected for both dimensions.

# Plot controls and supplied defaults, 2026-10-26

Width/depth regressions and the mean-HSI switch now sit above the map. Icon
buttons on the map fit the network and zoom to the selected segment. Editable
segment values replace the override switches; maximum depth is optional and
blank initially. Mean mode disables monthly controls, hides monthly segment
results and markers, and shows a full-curve mean point. The supplied CSVs enable
the GIFT Shiny Default preset without changing their values. No dependencies,
hosting settings, original model equations, or GeoPackages were changed.

# Network options update, 2026-10-26

Default CHaMP startup still uses the bundled overview without hydraulic solves.
Alternative network regressions and full-flow averaging now calculate all
segments only when selected, with separate bounded physical and habitat caches.
Local overrides remain segment-only. Dependencies and hosting configuration
are unchanged. See `HYDRAULIC_OPTIONS.md` and `VALIDATION.md` for details,
performance measurements, and the supplied default-curve provenance.

# On-demand hydraulic curves

This is the complete updated application, including the earlier deployment fixes.
The input data and monthly map overview are included. Detailed curves are
calculated for the selected segment and cached in the running server.

## Install or update

1. Extract the complete `gift-streamlit` folder and open it in VSCode.
2. When copying into an existing repository, overwrite matching application
   files and remove the old root `environment.yml`. The local Conda file is now
   `dev/environment.yml`. The old `data/hydraulics/` folder can also be removed;
   the webapp does not read or validate those native files.
3. In the existing Python 3.12 environment, install `requirements.txt`, then
   launch `run_streamlit.py` directly in VSCode. For a new environment, run
   `conda env create -f dev/environment.yml` and activate `gift_streamlit`.
4. Commit and push the updated source, overview files, and any legacy-file
   deletions. In Streamlit Community Cloud, use `app.py` as the entrypoint and
   Python 3.12 in Advanced settings.

No preparation run is needed for the supplied inputs. `prepare_data.py` remains
an explicit offline option to refresh the complete monthly overview after input
changes. Running it also produces optional native files that the webapp ignores.

## Startup and selection behavior

- Startup performs zero hydraulic curve solves and does not select a segment.
- The map retains the prepared monthly overview for matching source inputs.
- Clicking a segment or comparison point calculates that segment's hydraulic
  curve and evaluates its monthly habitat results and flow-response plots.
- Repeat clicks reuse cached hydraulics. Changing the life stage, months, or
  display units also reuses the physical solution.
- Physical cache entries are keyed by segment, hydraulic inputs, and calculation
  code. The physical cache holds at most 256 entries; habitat caches are also
  bounded. Server restarts clear these caches, and old entries can be evicted.
- Changed inputs are read without a network hydraulic rebuild. Reaches without
  matching overview values are gray until selected. Their calculated results
  update the map, comparisons, and network export for that user's session.
- Missing or corrupt overview files also leave hydraulics deferred to selection.
  Invalid source paths, layers, or required fields produce an input error.
- The original GIFT equations, uncapped shape factor, flow-range rules, and
  scientific reference tolerances are retained.

## Check the update

Install `pytest` for development checks, then run `run_checks.py` in VSCode.
The suite verifies zero startup solves, one solve for each newly selected reach,
cache reuse, changed inputs, overview updates, missing native files, and R-derived
scientific references. `VALIDATION.md` records the completed verification.

The first hosting build can still spend time installing Python dependencies.
This change removes bulk hydraulic work from application startup; it does not
control the hosting platform's build queue, package downloads, or instance startup.
The Docker image and concurrent browser users were not tested here.
