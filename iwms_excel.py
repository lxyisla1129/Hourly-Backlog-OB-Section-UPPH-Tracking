"""Map verified iWMS Excel headers to the existing productivity schema."""

from iwms_browser import ExportError, read_export
from iwms_normalize import normalize

WAREHOUSES = {"LAX1", "LAX2", "LAX4", "LAX5"}
REPORT_COLUMNS = {
    "picking": {
        "employee": "Staff NO.",
        "customer": "Owner Name",
        "start_time": "Task Receiving Time",
        "end_time": "Picking Completion Time",
        "units": "Actual Picking QTY.",
    },
    "packing": {
        "employee": "Rechecker",
        "customer": "Owner Name",
        "end_time": "Recheck Time",
        "units": "Rechecked QTY",
    },
}


def normalize_export(path, workflow, warehouse, *, sheet=0, header=0):
    """Load a single warehouse export using the existing iWMS normalization logic."""
    if workflow not in REPORT_COLUMNS or warehouse not in WAREHOUSES:
        raise ExportError("Choose a supported workflow and warehouse.")
    columns = REPORT_COLUMNS[workflow]
    rows = read_export(
        path, sheet=sheet, header=header, required_columns=columns.values()
    )
    for index, row in enumerate(rows):
        # Keep every detail line. This ID is scoped to a single workbook.
        row["_export_row"] = f"{warehouse}:{workflow}:{index}"
    report = {"columns": {**columns, "event_id": "_export_row"}}
    frame = normalize(rows, report, workflow, warehouse)
    if rows and frame.empty:
        raise ExportError("The workbook contains rows, but none passed iWMS normalization.")
    return frame
