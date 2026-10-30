# Run this file in VSCode's R session or RStudio once after installing R.
script_arg <- grep("^--file=", commandArgs(), value=TRUE)
source_file <- tryCatch(sys.frame(1)$ofile, error=function(e) NULL)
root <- if (length(script_arg)) dirname(normalizePath(sub("^--file=", "", script_arg[[1]]))) else
  if (!is.null(source_file)) dirname(normalizePath(source_file)) else normalizePath(".")
if (!file.exists(file.path(root, "r_gift", "worker.R"))) stop("Open the gift-streamlit project folder before running setup_r.R")
packages <- c("dplyr", "zoo", "jsonlite")
missing <- packages[!vapply(packages, requireNamespace, quietly=TRUE, FUN.VALUE=logical(1))]
if (length(missing)) install.packages(missing, repos="https://cloud.r-project.org")
if (!requireNamespace("birk", quietly=TRUE)) {
  install.packages(file.path(root, "r_gift", "dependencies", "birk_2.1.2.tar.gz"),
                   repos=NULL, type="source")
}
needed <- c(packages, "birk")
missing <- needed[!vapply(needed, requireNamespace, quietly=TRUE, FUN.VALUE=logical(1))]
if (length(missing)) stop("R setup incomplete. Missing packages: ", paste(missing, collapse=", "))
cat("GIFT R dependencies are ready.\n", R.version.string, "\n", sep="")
print(vapply(needed, function(name) as.character(packageVersion(name)), character(1)))
