"""Standardize iWMS report rows for the productivity dashboard."""
import pandas as pd


def normalize(raw_rows, report, workflow, warehouse):
    columns = report["columns"]
    required = ["event_id", "employee", "customer", "end_time", "units"]
    missing = [key for key in required if not columns.get(key)]
    if workflow == "picking" and not columns.get("start_time"):
        missing.append("start_time")
    if missing:
        raise ValueError("Unconfigured field mappings: " + ", ".join(missing))
    if not raw_rows:
        return pd.DataFrame(columns=["event_id", "employee", "customer", "start_time", "end_time", "units", "workflow", "warehouse"])
    df = pd.DataFrame(raw_rows)
    # iWMS taskPageNo is repeated across multiple order/SKU lines.
    # Build a row-level key from the order, SKU, task, location and completion time.
    # This is provisional until a native unique event ID is verified.
    if columns.get("event_id") == "__composite__":
        parts = ["taskPageNo", "outboundNo", "goodsNo", "cellNo", "updateTime", "employeeNo"]
        if any(part not in df.columns for part in parts):
            raise ValueError("Missing composite key fields")
        df["__composite__"] = df[parts].fillna("").astype(str).agg("|".join, axis=1)
    if columns.get("event_id") == "__packing_row__":
        # SKU-detail rows: the same package can contain multiple distinct goods.
        # Preserve all returned rows; index is local to this snapshot, not a persistent ID.
        df["__packing_row__"] = [f"{warehouse}:packing:{i}" for i in range(len(df))]
    mapped = {}
    for target, source in columns.items():
        if source:
            if source not in df.columns:
                raise ValueError(f"Response missing mapped column for {target}: {source}")
            mapped[target] = df[source]
    result = pd.DataFrame(mapped)
    if "start_time" not in result:
        # Packing only provides one completion timestamp; min/max is derived per employee.
        result["start_time"] = result["end_time"]
    for field in ("start_time", "end_time"):
        result[field] = pd.to_datetime(result[field], errors="coerce")
    result["units"] = pd.to_numeric(result["units"], errors="coerce")
    result["employee"] = result["employee"].astype("string")
    result["customer"] = result["customer"].astype("string")
    result["event_id"] = result["event_id"].astype("string")
    result = result.dropna(subset=["event_id", "employee", "start_time", "end_time", "units"])
    result = result[(result["units"] >= 0) & (result["end_time"] >= result["start_time"])]
    # Do not drop duplicate-looking rows without a verified native record key.
    result["workflow"] = workflow
    result["warehouse"] = warehouse
    return result
