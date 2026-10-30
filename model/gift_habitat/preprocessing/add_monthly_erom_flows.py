"""Add gage-adjusted NHDPlusV2 monthly EROM flows to a GeoPackage by COMID.

Run this file directly in VSCode after checking the settings below.

The supplied NHDPlusAttributes files do not contain monthly flow estimates.
This script uses EROM_010001.dbf through EROM_120001.dbf from EROMExtension.
If those tables are missing, it downloads the official Pacific Northwest
(VPU 17) EROMExtension archive. Automatic .7z extraction requires py7zr;
using already-extracted DBFs requires only Python's standard library.

Outputs are 1971-2000 long-term monthly means, not a monthly time series.
Q0001E is the final gage-adjusted estimate in cubic feet per second. Known
withdrawals/transfers and gage corrections are already included where
represented by EROM. Do not subtract PlusFlowAR quantities a second time.

The output is a copy of the entire input GeoPackage. Geometry, feature IDs,
spatial indexes, original attributes, and other layers are preserved.

Documentation:
https://www.epa.gov/system/files/documents/2023-04/NHDPlusV2_User_Guide.pdf
https://www.epa.gov/waterdata/nhdplus-pacific-northwest-data-vector-processing-unit-17
"""

from __future__ import annotations

import math
import os
import re
import sqlite3
import struct
import tempfile
import time
from contextlib import closing
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path, PurePosixPath
from urllib.request import urlopen


# -----------------------------------------------------------------------------
# USER SETTINGS
# -----------------------------------------------------------------------------

STREAMS_GPKG = Path(
    r"C:\Users\AlexThornton-Dunwood\OneDrive - Lichen Land & Water\Documents\GitHub"
    r"\gift-python\examples\Inputs\UGR_channel_geometry_predictions.gpkg"
)

# Set to None to select the only feature layer containing the COMID field.
STREAM_LAYER = "channel_geometry_predictions"
COMID_FIELD = "COMID"

NHD_ATTRIBUTES_FOLDER = Path(
    r"C:\L\Lichen\Lichen - Documents\Projects\20250009_BPA TSC\CRB_Prior"
    r"\07_GIS\0_Data_In\Public\Provisional Model\NHDPlusV2\NHDPlusAttributes"
)

# EROMExtension is a separate download, normally beside NHDPlusAttributes.
EROM_FOLDER = NHD_ATTRIBUTES_FOLDER.parent / "EROMExtension"
DOWNLOAD_IF_MISSING = True

# Official archive verified for the Upper Grande Ronde network, in VPU 17.
EROM_ARCHIVE_URL = (
    "https://dmap-data-commons-ow.s3.amazonaws.com/NHDPlusV21/Data/NHDPlusPN/"
    "NHDPlusV21_PN_17_EROMExtension_09.7z"
)

OUTPUT_GPKG = STREAMS_GPKG.with_name(STREAMS_GPKG.stem + "_monthly_EROM.gpkg")
OVERWRITE_OUTPUT = True
ADD_M3S_FIELDS = True


# -----------------------------------------------------------------------------
# CONSTANTS AND INPUT CHECKS
# -----------------------------------------------------------------------------

MONTHS = ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")
CFS_TO_M3S = 0.028316846592
EROM_FLOW_FIELD = "Q0001E"
TABLE_PATTERN = re.compile(r"EROM_(0[1-9]|1[0-2])0001\.dbf", re.IGNORECASE)
SOURCE_TABLE = "erom_monthly_sources"


def quote_identifier(name: str) -> str:
    """Quote SQL identifiers, including layer names containing spaces."""
    return '"' + name.replace('"', '""') + '"'


def open_readonly(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)


def normalize_comid(value) -> int | None:
    """Handle integer, floating-point, and text COMIDs without truncation."""
    if value is None or str(value).strip() == "":
        return None
    try:
        number = Decimal(str(value).strip())
    except InvalidOperation as exc:
        raise ValueError(f"Invalid COMID: {value!r}") from exc
    if not number.is_finite() or number != number.to_integral_value() or number <= 0:
        raise ValueError(f"COMID must be a positive integer: {value!r}")
    return int(number)


def inspect_network(path: Path, layer: str | None, comid_field: str):
    """Resolve the layer/field and collect unique identifiers for the join."""
    if not path.is_file():
        raise FileNotFoundError(f"Stream GeoPackage not found: {path}")
    with closing(open_readonly(path)) as connection:
        layers = [row[0] for row in connection.execute(
            "SELECT table_name FROM gpkg_contents WHERE data_type='features'"
        )]
        candidates = []
        for name in layers:
            columns = connection.execute(f"PRAGMA table_info({quote_identifier(name)})").fetchall()
            matches = [row[1] for row in columns if row[1].lower() == comid_field.lower()]
            if matches and (layer is None or name == layer):
                candidates.append((name, matches[0]))
        if len(candidates) != 1:
            raise ValueError(
                f"Select one feature layer containing {comid_field!r}. "
                f"Available layers: {layers}; matching layers: {candidates}"
            )
        name, actual_field = candidates[0]
        identifiers = [normalize_comid(row[0]) for row in connection.execute(
            f"SELECT {quote_identifier(actual_field)} FROM {quote_identifier(name)}"
        )]
    wanted = {value for value in identifiers if value is not None}
    if not wanted:
        raise ValueError("The stream network contains no valid COMIDs.")
    print(f"Layer: {name}; features: {len(identifiers):,}; unique COMIDs: {len(wanted):,}")
    if len(identifiers) - sum(value is not None for value in identifiers):
        print("Warning: features with missing COMIDs will have NULL flow values.")
    return name, actual_field, wanted, len(identifiers)


# -----------------------------------------------------------------------------
# LOCATE OR DOWNLOAD THE MONTHLY EROM TABLES
# -----------------------------------------------------------------------------

def find_monthly_tables(folder: Path, exclude_cache: bool = False) -> dict[int, Path]:
    files = {}
    if not folder.exists():
        return files
    for path in sorted(folder.rglob("*")):
        if not path.is_file():
            continue
        if exclude_cache and "_downloaded_vpu17_09" in path.relative_to(folder).parts:
            continue
        match = TABLE_PATTERN.fullmatch(path.name)
        if match:
            month = int(match.group(1))
            if month in files:
                raise ValueError(
                    f"Multiple EROM tables for month {month:02d}:\n{files[month]}\n{path}\n"
                    "Set EROM_FOLDER to a single, consistent EROMExtension release."
                )
            files[month] = path
    return files


def download_archive(url: str, target: Path) -> None:
    """Cache the archive; incomplete downloads never replace the cached file."""
    if target.is_file():
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_suffix(target.suffix + ".part")
    print(f"Downloading EROMExtension from:\n{url}", flush=True)
    try:
        with urlopen(url, timeout=90) as response, partial.open("wb") as destination:
            total = int(response.headers.get("Content-Length", 0))
            downloaded = 0
            last_report = time.monotonic()
            while block := response.read(1024 * 1024):
                destination.write(block)
                downloaded += len(block)
                if time.monotonic() - last_report >= 10:
                    print(f"  Downloaded {downloaded / 1e6:.1f} / {total / 1e6:.1f} MB", flush=True)
                    last_report = time.monotonic()
            if total and downloaded != total:
                raise IOError(f"Incomplete download: received {downloaded} of {total} bytes.")
        partial.replace(target)
    except Exception:
        partial.unlink(missing_ok=True)
        raise


def get_monthly_tables(folder: Path, allow_download: bool) -> dict[int, Path]:
    # Prefer a complete set of user-supplied tables over the download cache.
    local = find_monthly_tables(folder, exclude_cache=True)
    if len(local) == 12:
        return local
    cached_folder = folder / "_downloaded_vpu17_09"
    cached = find_monthly_tables(cached_folder)
    if len(cached) == 12:
        return cached
    missing = [MONTHS[m - 1] for m in range(1, 13) if m not in local]
    if not allow_download:
        raise FileNotFoundError(
            f"Missing monthly EROM tables in {folder}: {', '.join(missing)}.\n"
            "The NHDPlusAttributes DBFs cannot supply these flows. Download and extract "
            "the EROMExtension archive, or set DOWNLOAD_IF_MISSING=True."
        )
    try:
        import py7zr
    except ImportError as exc:
        raise ImportError(
            "Automatic .7z extraction needs py7zr in the VSCode Python environment. "
            "Install py7zr, or extract the archive with 7-Zip and set EROM_FOLDER "
            "to the folder containing the 12 monthly DBFs."
        ) from exc
    archive = folder / "_downloads" / EROM_ARCHIVE_URL.rsplit("/", 1)[-1]
    download_archive(EROM_ARCHIVE_URL, archive)
    print("Extracting the 12 monthly EROM tables...", flush=True)
    with py7zr.SevenZipFile(archive, mode="r") as source:
        targets = [name for name in source.getnames()
                   if TABLE_PATTERN.fullmatch(PurePosixPath(name).name)]
        if len(targets) != 12:
            raise ValueError(f"Expected 12 monthly tables in {archive}; found {len(targets)}.")
        for name in targets:
            if ".." in PurePosixPath(name).parts or PurePosixPath(name).is_absolute():
                raise ValueError(f"Unsafe archive member: {name}")
        source.extract(path=cached_folder, targets=targets)
    result = find_monthly_tables(cached_folder)
    if len(result) != 12:
        raise ValueError("Extraction did not produce a complete set of monthly EROM tables.")
    return result


# -----------------------------------------------------------------------------
# READ ONLY THE COMID AND FINAL FLOW FIELD FROM EACH DBF
# -----------------------------------------------------------------------------

def read_month_flows(path: Path, wanted: set[int]) -> dict[int, float | None]:
    """Stream numeric EROM DBF fields without loading all regional attributes.

    EROM uses dBASE numeric/float fields. Reading just the required fields keeps
    memory use low even when each regional table contains hundreds of thousands
    of rows. Deleted records are ignored and documented missing codes become NULL.
    """
    result = {}
    with path.open("rb") as source:
        header = source.read(32)
        if len(header) != 32:
            raise ValueError(f"Invalid DBF header: {path}")
        record_count, header_length, record_length = struct.unpack_from("<IHH", header, 4)
        if header_length < 33 or record_length < 2:
            raise ValueError(f"Invalid DBF dimensions: {path}")
        descriptors = source.read(header_length - 32)
        fields = {}
        offset = 1  # First byte of a DBF row is the deleted-record flag.
        for position in range(0, len(descriptors), 32):
            if descriptors[position:position + 1] == b"\r":
                break
            descriptor = descriptors[position:position + 32]
            if len(descriptor) != 32:
                raise ValueError(f"Invalid DBF field descriptor: {path}")
            name = descriptor[:11].split(b"\0", 1)[0].decode("ascii").upper()
            length = descriptor[16]
            fields[name] = (offset, length, chr(descriptor[11]))
            offset += length
        for name in ("COMID", EROM_FLOW_FIELD):
            if name not in fields or fields[name][2] not in ("N", "F"):
                raise ValueError(f"Required numeric field {name!r} not found in {path}.")
        if offset > record_length:
            raise ValueError(f"DBF field widths exceed the record length: {path}")
        id_offset, id_length, _ = fields["COMID"]
        q_offset, q_length, _ = fields[EROM_FLOW_FIELD]
        for _ in range(record_count):
            record = source.read(record_length)
            if len(record) != record_length:
                raise IOError(f"Truncated DBF record in {path}")
            if record[:1] == b"*":
                continue
            comid = normalize_comid(record[id_offset:id_offset + id_length].decode("ascii"))
            if comid not in wanted:
                continue
            text = record[q_offset:q_offset + q_length].decode("ascii").strip()
            flow = float(text) if text else None
            if flow is not None and (flow in (-9998.0, -9999.0) or not math.isfinite(flow)):
                flow = None
            if flow is not None and flow < 0:
                raise ValueError(f"Unexpected negative flow for COMID {comid} in {path}: {flow}")
            if comid in result:
                raise ValueError(f"Duplicate COMID {comid} in monthly table {path}")
            result[comid] = flow
    return result


# -----------------------------------------------------------------------------
# UPDATE A COPY OF THE GEOPACKAGE AND VERIFY THE JOIN
# -----------------------------------------------------------------------------

def register_gpkg_functions(connection: sqlite3.Connection) -> None:
    """Supply the spatial functions referenced by standard GeoPackage triggers.

    Only non-geometric attributes are updated. The feature-ID/geometry-change
    triggers do not run, but SQLite still resolves their function names. These
    implementations read the GeoPackage header/envelope without rewriting WKB.
    """
    def is_empty(blob):
        return blob is None or bool(blob[3] & 0x10)

    def envelope_value(blob, index):
        if blob is None:
            return None
        if blob[:2] != b"GP" or not ((blob[3] >> 1) & 7):
            raise ValueError("Geometry lacks a GeoPackage envelope.")
        byte_order = "<" if blob[3] & 1 else ">"
        return struct.unpack_from(byte_order + "4d", blob, 8)[index]

    connection.create_function("ST_IsEmpty", 1, is_empty)
    for index, name in enumerate(("ST_MinX", "ST_MaxX", "ST_MinY", "ST_MaxY")):
        connection.create_function(name, 1, lambda blob, i=index: envelope_value(blob, i))


def write_output(input_path, output_path, layer, comid_field, wanted, month_flows,
                 month_tables, add_m3s=True, overwrite=True):
    if input_path.resolve() == output_path.resolve():
        raise ValueError("OUTPUT_GPKG must differ from the input GeoPackage.")
    if output_path.exists() and not overwrite:
        raise FileExistsError(f"Output exists and OVERWRITE_OUTPUT=False: {output_path}")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=output_path.stem + "_", suffix=".gpkg", dir=output_path.parent
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    columns = [(f"erom_q_{month}_cfs", number, 1.0)
               for number, month in enumerate(MONTHS, 1)]
    if add_m3s:
        columns += [(f"erom_q_{month}_m3s", number, CFS_TO_M3S)
                    for number, month in enumerate(MONTHS, 1)]
    field_names = [name for name, _, _ in columns] + ["erom_months_available"]
    table = quote_identifier(layer)
    try:
        with closing(open_readonly(input_path)) as original, \
                closing(sqlite3.connect(temporary, uri=True)) as destination:
            original.backup(destination)
            register_gpkg_functions(destination)
            destination.create_function("erom_comid", 1, normalize_comid)
            destination.execute("PRAGMA foreign_keys=ON")
            with destination:
                existing = {row[1].lower(): row[1] for row in destination.execute(
                    f"PRAGMA table_info({table})"
                )}
                for name in field_names:
                    if name.lower() not in existing:
                        sql_type = "INTEGER" if name == "erom_months_available" else "REAL"
                        destination.execute(f"ALTER TABLE {table} ADD COLUMN {quote_identifier(name)} {sql_type}")
                definitions = ", ".join(
                    f"{quote_identifier(name)} REAL" for name, _, _ in columns
                )
                destination.execute(
                    f"CREATE TEMP TABLE erom_join (comid INTEGER PRIMARY KEY, {definitions}, "
                    "erom_months_available INTEGER)"
                )
                records = []
                for comid in sorted(wanted):
                    values = []
                    for _, month, multiplier in columns:
                        flow = month_flows[month].get(comid)
                        values.append(None if flow is None else flow * multiplier)
                    count = sum(month_flows[m].get(comid) is not None for m in range(1, 13))
                    records.append((comid, *values, count))
                placeholders = ",".join("?" for _ in range(len(field_names) + 1))
                destination.executemany(f"INSERT INTO erom_join VALUES ({placeholders})", records)
                condition = f"erom_join.comid = erom_comid({table}.{quote_identifier(comid_field)})"
                assignments = []
                for name in field_names:
                    value = f"(SELECT {quote_identifier(name)} FROM erom_join WHERE {condition})"
                    if name == "erom_months_available":
                        value = f"COALESCE({value}, 0)"
                    assignments.append(f"{quote_identifier(name)} = {value}")
                destination.execute(f"UPDATE {table} SET " + ", ".join(assignments))
                timestamp = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
                destination.execute("UPDATE gpkg_contents SET last_change=? WHERE table_name=?", (timestamp, layer))
                # Register a non-spatial provenance table alongside the feature layer.
                destination.execute(f"DROP TABLE IF EXISTS {quote_identifier(SOURCE_TABLE)}")
                destination.execute(
                    f"CREATE TABLE {quote_identifier(SOURCE_TABLE)} (id INTEGER PRIMARY KEY, "
                    "month INTEGER, month_name TEXT, feature_layer TEXT, source_file TEXT, "
                    "source_field TEXT, reference_period TEXT, cfs_field TEXT, m3s_field TEXT, "
                    "source_archive_url TEXT, interpretation TEXT)"
                )
                interpretation = (
                    "Long-term monthly mean; gage-adjusted EROM. Includes known flow alterations "
                    "and gage corrections where represented; incomplete accounting of diversions."
                )
                destination.executemany(
                    f"INSERT INTO {quote_identifier(SOURCE_TABLE)} VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    [(m, m, MONTHS[m - 1], layer, str(month_tables[m]), EROM_FLOW_FIELD,
                      "1971-2000", f"erom_q_{MONTHS[m - 1]}_cfs",
                      f"erom_q_{MONTHS[m - 1]}_m3s" if add_m3s else None,
                      EROM_ARCHIVE_URL if "_downloaded_vpu17_09" in month_tables[m].parts else None,
                      interpretation) for m in range(1, 13)]
                )
                destination.execute("DELETE FROM gpkg_contents WHERE table_name=?", (SOURCE_TABLE,))
                destination.execute(
                    "INSERT INTO gpkg_contents (table_name,data_type,identifier,description,last_change) "
                    "VALUES (?, 'attributes', ?, ?, ?)",
                    (SOURCE_TABLE, SOURCE_TABLE, "Monthly EROM source and field definitions", timestamp)
                )
            # Compare original columns in both directions, including the geometry bytes.
            original_columns = [row[1] for row in original.execute(f"PRAGMA table_info({table})")
                                if row[1].lower() not in {name.lower() for name in field_names}]
            comparison_columns = ",".join(quote_identifier(name) for name in original_columns)
            destination.execute("ATTACH DATABASE ? AS original_file", (input_path.resolve().as_uri() + "?mode=ro",))
            for left, right in (("main", "original_file"), ("original_file", "main")):
                difference = destination.execute(
                    f"SELECT {comparison_columns} FROM {left}.{table} EXCEPT "
                    f"SELECT {comparison_columns} FROM {right}.{table} LIMIT 1"
                ).fetchone()
                if difference is not None:
                    raise AssertionError("An original feature, geometry, or attribute changed.")
            destination.execute("DETACH DATABASE original_file")
            if destination.execute("PRAGMA integrity_check").fetchone() != ("ok",):
                raise AssertionError("Output failed SQLite integrity_check.")
            if destination.execute("PRAGMA foreign_key_check").fetchone() is not None:
                raise AssertionError("Output contains a foreign-key violation.")
            count_before = original.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            if destination.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] != count_before:
                raise AssertionError("Feature count changed during the join.")
        os.replace(temporary, output_path)
    finally:
        temporary.unlink(missing_ok=True)


def main():
    layer, field, wanted, feature_count = inspect_network(STREAMS_GPKG, STREAM_LAYER, COMID_FIELD)
    tables = get_monthly_tables(EROM_FOLDER, DOWNLOAD_IF_MISSING)
    flows = {}
    print("Reading gage-adjusted monthly flow estimates (Q0001E; cfs)...", flush=True)
    for month in range(1, 13):
        flows[month] = read_month_flows(tables[month], wanted)
        valid = sum(value is not None for value in flows[month].values())
        zero = sum(value == 0 for value in flows[month].values() if value is not None)
        print(f"  {MONTHS[month - 1].title()}: {valid:,}/{len(wanted):,} COMIDs with valid flows; "
              f"{zero:,} zero-flow estimates", flush=True)
    complete = sum(all(flows[m].get(comid) is not None for m in range(1, 13)) for comid in wanted)
    if not any(value is not None for values in flows.values() for value in values.values()):
        raise ValueError("No usable monthly flows matched. Check the COMIDs and EROM region/release.")
    write_output(STREAMS_GPKG, OUTPUT_GPKG, layer, field, wanted, flows, tables,
                 ADD_M3S_FIELDS, OVERWRITE_OUTPUT)
    print(f"\nSaved {feature_count:,} features: {OUTPUT_GPKG}")
    print(f"COMIDs with all 12 monthly flows: {complete:,}/{len(wanted):,}")
    if complete < len(wanted):
        print("Warning: unmatched or unavailable estimates remain NULL, never zero.")
    print("Flow fields: erom_q_jan_cfs ... erom_q_dec_cfs")
    if ADD_M3S_FIELDS:
        print("Metric fields: erom_q_jan_m3s ... erom_q_dec_m3s")
    print("Reference period: 1971-2000; monthly sources are recorded in erom_monthly_sources.")


if __name__ == "__main__":
    main()
