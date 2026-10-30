# JSON-line worker for the pinned, unmodified original GIFT R functions.
# All calculation calls run in this process, outside Streamlit's threads.
args <- commandArgs(trailingOnly=TRUE)
root <- normalizePath(args[[1]], mustWork=TRUE)
needed <- c("jsonlite", "dplyr", "zoo", "birk")
if (!requireNamespace("jsonlite", quietly=TRUE)) {
  cat('{"id":0,"ok":false,"error":"Missing R package jsonlite. Run setup_r.R in R."}\n')
  quit(status=1)
}
send <- function(value) {
  cat(jsonlite::toJSON(value, auto_unbox=TRUE, dataframe="columns",
                      digits=NA, na="null", null="null"), "\n", sep="")
  flush(stdout())
}
missing <- needed[!vapply(needed, requireNamespace, quietly=TRUE, FUN.VALUE=logical(1))]
if (identical(missing, "birk")) {
  # Community Cloud installs the other packages through packages.txt. Install
  # the bundled pure-R dependency locally, without downloading anything.
  local_library <- file.path(tools::R_user_dir("gift-streamlit", "cache"), "R-library")
  dir.create(local_library, recursive=TRUE, showWarnings=FALSE)
  .libPaths(c(local_library, .libPaths()), include.site=FALSE)
  if (!requireNamespace("birk", quietly=TRUE)) {
    install_log <- tryCatch(system2(file.path(R.home("bin"), "R"),
      c("CMD", "INSTALL", paste0("--library=", shQuote(local_library)),
        shQuote(file.path(root, "dependencies", "birk_2.1.2.tar.gz"))),
      stdout=TRUE, stderr=TRUE), error=function(e) conditionMessage(e))
  }
  missing <- needed[!vapply(needed, requireNamespace, quietly=TRUE, FUN.VALUE=logical(1))]
}
if (length(missing)) {
  send(list(id=0, ok=FALSE, error=paste("Missing R packages:", paste(missing, collapse=", "),
                                     ". Run setup_r.R in R.")))
  quit(status=1)
}
suppressPackageStartupMessages({
  source(file.path(root, "upstream", "AvgHydraulics.R"), local=TRUE)
  source(file.path(root, "upstream", "Habitat.R"), local=TRUE)
})

# Change only the return expression of an in-memory copy. Every original
# numeric statement executes unchanged. This exposes already-computed native
# stages and the exact cross-section grid used by the original function.
NativeHydraulics <- AvgHydraulics
stopifnot(identical(tail(as.list(body(NativeHydraulics)), 1)[[1]], quote(return(mod_hyd))))
body(NativeHydraulics)[[length(body(NativeHydraulics))]] <-
  quote(return(list(native=simulated, grid=mod_hyd, shape=b, maximum=dmax,
                    x=Xgrid, y=Ygrid - 5 * db)))
physical_cache <- new.env(parent=emptyenv())
physical_keys <- character()

physical <- function(parameters) {
  key <- jsonlite::toJSON(parameters, auto_unbox=TRUE, digits=NA, null="null")
  if (exists(key, envir=physical_cache, inherits=FALSE)) return(get(key, physical_cache))
  call <- list(S=parameters$slope, wb=parameters$width, db=parameters$depth,
               D84=parameters$d84, xs_output=FALSE)
  if (!is.null(parameters$maximum)) call$db_max <- parameters$maximum
  if (!is.null(parameters$shape)) call$b_value <- parameters$shape
  out <- do.call(NativeHydraulics, call)
  native <- out$native
  native <- native[complete.cases(native) & rowSums(native > 0, na.rm=TRUE) == ncol(native), , drop=FALSE]
  out$native <- native[order(native$Q, method="radix"), , drop=FALSE]
  if (!nrow(out$native)) stop("No valid native hydraulic stages")
  assign(key, out, envir=physical_cache)
  physical_keys <<- c(physical_keys, key)
  if (length(physical_keys) > 32) {
    rm(list=physical_keys[[1]], envir=physical_cache)
    physical_keys <<- physical_keys[-1]
  }
  out
}
as_frame <- function(value) {
  as.data.frame(lapply(value, function(column) {
    vapply(column, function(x) if (is.null(x)) NA_real_ else as.numeric(x), numeric(1))
  }), check.names=FALSE)
}
table_result <- function(frame, attrs=list()) {
  list(columns=I(names(frame)), data=unname(split(as.matrix(frame), row(frame))), attrs=attrs)
}
dispatch <- function(request) {
  if (request$operation == "hydraulics" || request$operation == "geometry") {
    out <- physical(request$parameters)
    if (request$operation == "geometry") {
      return(list(x=I(out$x), y=I(out$y), maximum=out$maximum, shape=out$shape))
    }
    if (isTRUE(request$full_curve)) {
      result <- out$native
    } else if (!is.null(request$discharges)) {
      q <- unlist(request$discharges)
      result <- data.frame(Q=q)
      for (column in c("Ai", "Wi", "di", "Ui"))
        result[[column]] <- approx(out$native$Q, out$native[[column]], xout=q)$y
      result <- result[!is.na(result$Ai), , drop=FALSE]
    } else {
      result <- out$grid
    }
    return(table_result(result, list(shape_factor=out$shape, shape_factor_raw=out$shape,
      shape_factor_capped=FALSE, shape_factor_above_0_7=out$shape > 0.7,
      simulated_max_depth_m=out$maximum,
      simulated_min_discharge_m3s=min(out$native$Q),
      simulated_bankfull_discharge_m3s=max(out$native$Q))))
  }
  if (request$operation == "habitat") {
    hyd <- as_frame(request$hydraulics)
    dc <- as_frame(request$depth_curve)
    vc <- as_frame(request$velocity_curve)
    sc <- if (is.null(request$substrate_curve)) NULL else as_frame(request$substrate_curve)
    gsd <- if (is.null(request$gsd)) NULL else unlist(request$gsd)
    out <- Habitat(hyd, dc, vc, sc, gsd, wua_output=FALSE)
    return(table_result(out))
  }
  stop("Unknown GIFT worker operation")
}
versions <- as.list(vapply(needed, function(package) as.character(packageVersion(package)), character(1)))
send(list(id=0, ok=TRUE, result=list(R_version=R.version.string, packages=versions)))
input <- file("stdin", open="r")
repeat {
  line <- readLines(input, n=1, warn=FALSE)
  if (!length(line)) break
  request <- NULL
  response <- tryCatch({
    request <- jsonlite::fromJSON(line, simplifyVector=FALSE)
    value <- suppressPackageStartupMessages(suppressWarnings(dispatch(request)))
    list(id=request$id, ok=TRUE, result=value)
  }, error=function(e) list(id=if (is.null(request)) -1 else request$id,
                           ok=FALSE, error=conditionMessage(e)))
  send(response)
}
close(input)
