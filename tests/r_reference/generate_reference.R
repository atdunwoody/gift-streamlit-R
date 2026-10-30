# Regenerate frozen GIFT references with R, independently of the Python model.
# In VSCode, open the project folder and source this file in an R session.
# Optional dependencies: install.packages(c("dplyr", "zoo", "birk"))
# Normal Python tests read the frozen CSVs and do not require R.

script_arg <- grep("^--file=", commandArgs(), value=TRUE)
file_flag <- match("-f", commandArgs())
if (!length(script_arg) && !is.na(file_flag)) {
  script_arg <- paste0("--file=", commandArgs()[file_flag+1L])
}
if (length(script_arg)) {
  reference_dir <- dirname(normalizePath(sub("^--file=", "", script_arg[1])))
} else {
  source_file <- tryCatch(sys.frame(1)$ofile, error=function(e) NULL)
  reference_dir <- if (!is.null(source_file)) dirname(normalizePath(source_file)) else
    normalizePath(file.path("tests", "r_reference"))
}
app_dir <- normalizePath(file.path(reference_dir, "..", ".."))
needed <- c("dplyr", "zoo", "birk")
missing <- needed[!vapply(needed, requireNamespace, quietly=TRUE, FUN.VALUE=logical(1))]
if (length(missing)) stop("Install the R packages: ", paste(missing, collapse=", "))

source(file.path(reference_dir, "source", "AvgHydraulics.R"))
source(file.path(reference_dir, "source", "Habitat.R"))
options(digits=17)

# Expose the native levels already computed by the source function. Only its
# final return expression changes; all numeric statements remain unmodified.
NativeHydraulics <- AvgHydraulics
body(NativeHydraulics)[[length(body(NativeHydraulics))]] <-
  quote(return(list(grid=mod_hyd, native=simulated, shape=b, max_depth=dmax)))

write_reference <- function(frame, filename) {
  write.csv(frame, file.path(reference_dir, filename), row.names=FALSE, na="")
}
model_args <- function(r) {
  args <- list(S=r$S, wb=r$wb, db=r$db, D84=r$D84, xs_output=FALSE)
  if (!is.na(r$db_max)) args$db_max <- r$db_max
  if (!is.na(r$b_value)) args$b_value <- r$b_value
  args
}

cases <- read.csv(file.path(reference_dir, "hydraulic_cases.csv"))
grids <- list(); natives <- list(); metadata <- list(); reach_models <- list()
for (i in seq_len(nrow(cases))) {
  r <- cases[i,]
  # The R source's try(stop(...)) prints for b > 0.7, but continues using b.
  out <- do.call(NativeHydraulics, model_args(r))
  if (nrow(out$grid)) grids[[length(grids)+1]] <- cbind(case=r$case, out$grid)
  # approx() already ignores non-finite source simulations. Exclude the same
  # unsupported levels from the native reference (not from the R equations).
  supported <- complete.cases(out$native) & rowSums(out$native > 0, na.rm=TRUE) == ncol(out$native)
  out$native <- out$native[supported,]
  ordered <- out$native[order(out$native$Q, method="radix"),]
  ranks <- unique(as.integer(round(seq(1, nrow(ordered), length.out=41))))
  natives[[i]] <- cbind(case=r$case, rank=ranks-1L, ordered[ranks,])
  metadata[[i]] <- data.frame(case=r$case, shape=out$shape, max_depth=out$max_depth,
    q_min=min(out$native$Q), q_max=max(out$native$Q), grid_count=nrow(out$grid))
  if (startsWith(r$case, "reach_")) reach_models[[sub("reach_", "", r$case)]] <- out
}
write_reference(do.call(rbind, grids), "hydraulics.csv")
write_reference(do.call(rbind, natives), "native_levels.csv")
write_reference(do.call(rbind, metadata), "hydraulic_metadata.csv")

curve_files <- list.files(file.path(app_dir, "inputs", "curves"),
                         pattern="_depth.csv$", full.names=TRUE)
hyd <- read.csv(file.path(reference_dir, "habitat_inputs.csv"))
habitat_rows <- list()
gsd <- c(1, 2, 5, 10, 20, 40, 60, 100, 200, 400)
monthly_rows <- list()
monthly_inputs <- read.csv(file.path(reference_dir, "monthly_inputs.csv"))
for (depth_file in curve_files) {
  key <- sub("_depth.csv$", "", basename(depth_file))
  dc <- read.csv(depth_file)
  vc <- read.csv(sub("_depth.csv$", "_velocity.csv", depth_file))
  sc <- read.csv(sub("_depth.csv$", "_substrate.csv", depth_file))
  for (mode in c("none", "distribution", "D84_proxy")) {
    args <- list(hydraulics=hyd, d_curve=dc, v_curve=vc, wua_output=FALSE)
    if (mode != "none") {
      args$s_curve <- sc
      args$gsd <- if (mode == "distribution") gsd else rep(100, 10)
    }
    result <- do.call(Habitat, args)
    habitat_rows[[length(habitat_rows)+1]] <- cbind(curve=key, substrate_mode=mode,
      input_row=seq_len(nrow(hyd))-1L, result)
  }

  # This is the application's monthly extension: interpolate R's native
  # hydraulics to each requested flow, evaluate Habitat separately at each,
  # and use a one-size GSD for the documented D84 proxy.
  for (comid in names(reach_models)) {
    model <- reach_models[[comid]]
    r <- cases[cases$case == paste0("reach_", comid),]
    qrows <- monthly_inputs[monthly_inputs$id == as.numeric(comid),]
    for (j in seq_len(nrow(qrows))) {
      q <- qrows$q[j]
      status <- if (is.na(q) || q < 0) "missing_flow" else if (q == 0) "zero_flow" else
        if (q < min(model$native$Q)) "below_range" else
        if (q > max(model$native$Q)) "above_bankfull" else "ok"
      sizes <- gsd * 0 + r$D84
      substrate <- sum(vapply(seq_len(nrow(sc)), function(k) {
        sum(sizes >= sc$lower[k] & sizes < sc$upper[k]) * sc$suit[k]
      }, numeric(1))) / sum(vapply(seq_len(nrow(sc)), function(k) {
        sum(sizes >= sc$lower[k] & sizes < sc$upper[k])
      }, numeric(1)))
      row <- data.frame(id=as.numeric(comid), curve=key, month=qrows$month[j], q=q,
        depth=NA_real_, velocity=NA_real_, width=NA_real_, d=NA_real_, v=NA_real_,
        s=substrate, hsi=NA_real_, wua=NA_real_, status=status)
      if (status == "zero_flow") {
        row[c("depth", "velocity", "width", "hsi", "wua")] <- 0
      } else if (status == "ok") {
        h <- as.data.frame(lapply(model$native[c("Ai", "Wi", "di", "Ui")], function(v) {
          approx(model$native$Q, v, xout=q)$y
        }))
        h$Q <- q
        result <- suppressWarnings(Habitat(h, dc, vc, sc, sizes, wua_output=FALSE))
        row$depth <- h$di; row$velocity <- h$Ui; row$width <- h$Wi
        row$d <- result$d.suit; row$v <- result$v.suit; row$s <- result$s.suit
        row$hsi <- result$d.suit * result$v.suit * result$s.suit
        row$wua <- result$WUA
      }
      monthly_rows[[length(monthly_rows)+1]] <- row
    }
  }
}
write_reference(do.call(rbind, habitat_rows), "habitat.csv")
write_reference(do.call(rbind, monthly_rows), "monthly.csv")
hash_files <- c(file.path(reference_dir, "source", c("AvgHydraulics.R", "Habitat.R")),
                list.files(file.path(app_dir, "inputs", "curves"), pattern=".csv$", full.names=TRUE))
write_reference(data.frame(file=basename(hash_files), md5=unname(tools::md5sum(hash_files))), "source_hashes.csv")
quote_json <- function(s) encodeString(s, quote='"')
writeLines(paste0('{\n  "source_repository": "https://github.com/SGronsdahl/GIFT",\n',
  '  "source_revision": "40c4a74f82d842cd2ab8ab4c53e2b985ac4e0030",\n',
  '  "R_version": ', quote_json(R.version.string), ',\n',
  '  "dplyr_version": ', quote_json(as.character(packageVersion("dplyr"))), ',\n',
  '  "zoo_version": ', quote_json(as.character(packageVersion("zoo"))), ',\n',
  '  "birk_version": ', quote_json(as.character(packageVersion("birk"))), '\n}'),
  file.path(reference_dir, "provenance.json"))
cat("Wrote R-derived references for", nrow(cases), "hydraulic cases,",
    nrow(do.call(rbind, habitat_rows)), "habitat rows, and",
    nrow(do.call(rbind, monthly_rows)), "monthly rows.\n")
