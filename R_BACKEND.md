# Original R GIFT backend

Streamlit calls the original `AvgHydraulics()` and `Habitat()` functions from
https://github.com/SGronsdahl/GIFT at commit
`40c4a74f82d842cd2ab8ab4c53e2b985ac4e0030`. Their unchanged source files are in
`r_gift/upstream/`. SHA-256 hashes are checked against `r_gift/provenance.json`.
The Python model under `model/` remains for independent regression checks and
legacy batch tools; the Streamlit calculation path has no Python fallback.

## Install and launch

Use `dev/environment.yml` for a combined Python/R Conda environment, or install
R separately and install the Python requirements. Run `setup_r.R` in an R
session once, then run `check_r.py` directly in VSCode. Launch with
`run_streamlit.py`. `RSCRIPT_PATH` in `settings.py` selects an explicit Rscript
executable; environment variable `GIFT_RSCRIPT` takes precedence. Standard
Windows R installation folders are also searched when Rscript is not on PATH.

R needs dplyr, zoo, jsonlite, and birk. The pure-R birk 2.1.2 source is bundled
from the CRAN mirror at https://github.com/cran/birk/tree/2.1.2, with its checksum
and GPL-3 license recorded in the provenance file. This is the birk version
used by the saved R references. If only birk is missing, the worker installs
that bundled package into a user cache without a download. The source package
does not need a C compiler or Rtools. Other missing packages produce an explicit
setup error. `setup_r.R` installs those packages through CRAN when requested.

The Dockerfile installs the R runtime and dependencies during the build.
Streamlit Community Cloud uses `packages.txt` for its system packages.

## Execution and preserved methods

`r_engine.py` owns one persistent Rscript worker per Python process. A lock
serializes requests from Streamlit sessions. JSON messages carry numeric tables
and results, while R messages go to a separate stderr reader. Request IDs,
timeouts, worker restart, and bounded calculation caches prevent interleaved
responses and repeated work. `GIFT_R_TIMEOUT` sets the per-request timeout in
seconds, default 300. R failures are displayed explicitly.

The original R hydraulic function returns an interpolated discharge table.
The worker changes only the final return expression of an in-memory copy to
expose the native `simulated` stages, shape factor, and cross-section grid.
All numeric statements in the original function execute unchanged. The
channel-geometry plot uses the cross-section grid returned by R.

The existing Python application still selects regressions, interpolates native
hydraulics to exact EROM monthly flows, applies the documented D84 substrate
proxy, handles unavailable and zero-flow cases, and summarizes results. R
`Habitat()` evaluates the depth and velocity distributions. Its component
scores are combined with the per-reach D84 suitability and wetted width.
Single-reach calls also support the original grain-size-distribution argument.
These application choices and the 201-sample, discharge-range mean remain
unchanged. R plot-file output is disabled; Streamlit renders its own figures.

## Prepared R calculations

`data/r_scenarios/` contains original-R calculations for CHaMP width/depth and
CHaMP width with Castro and Jackson mean depth. These include native hydraulic
curves and mean summaries for the supplied suitability curves. The source
overview in `data/network.json` is also recalculated with R. Prepared results
are used only when source fingerprints, model hashes, reach IDs, regressions,
region, suitability curves, and relevant file checksums match. Other scenarios
and edits execute R and are cached. Custom curves retain the same R path.

After source-data changes, run `prepare_r_scenarios.py` directly in VSCode. It
reads current configured inputs, solves both scenarios with separate R workers,
and refreshes the overview, native curves, means, and provenance. This can take
several minutes. Completed reach calculations are cached locally so interrupted
builds can resume. `WORKERS` and `PREPARE_MEAN_SUMMARIES` at the top of the script
control offline work; the webapp itself uses a single serialized R worker.
The optional original `prepare_data.py` workflow also uses the R backend.

Network and segment CSVs and diagnostic JSON identify the R engine and pinned
GIFT revision. Input notes show the live R version and package versions.

## Verification

Run `run_checks.py` in VSCode with R installed. The suite includes live R
hydraulics and habitat comparisons with frozen R results, independent Python
parity checks, concurrent requests, missing-runtime errors, worker restart,
source checksums, and Streamlit controls. `VALIDATION.md` records completed
checks. The numerical tolerances and original source files are retained.
