"""Order-level packing deadline metrics from the integrated outbound export."""
from zoneinfo import ZoneInfo
import pandas as pd
from iwms_calendar import backlog_window, weekly_backlog_window

TZ = ZoneInfo("America/Los_Angeles")
STAGES = {"Pending picking": "Picking Completion Time", "Pending recheck": "Recheck Time",
          "Pending packing": "Packed Time", "Pending handover": "Handover Time"}
REQUIRED = ["JD Order NO.", "Received Time", "SPB Name", *STAGES.values()]


def backlog_summary(raw, day, now=None, *, weekly=False):
    if set(REQUIRED).difference(raw.columns):
        raise ValueError("Integrated export is missing required order, receive, packed or SPB columns.")
    if raw["JD Order NO."].isna().any():
        raise ValueError("Some rows have no JD Order NO.; order totals cannot be verified.")
    frame = raw.copy()
    for column in ("Received Time", *STAGES.values()):
        values = pd.to_datetime(frame[column], errors="coerce")
        supplied = frame[column].notna() & frame[column].astype(str).str.strip().ne("")
        if (supplied & values.isna()).any():
            raise ValueError("Some report timestamps could not be parsed.")
        frame[column] = values.dt.tz_localize(TZ, ambiguous="raise", nonexistent="raise")
    if frame["Received Time"].isna().any():
        raise ValueError("Some orders have no Received Time.")
    frame["_excluded"] = frame["SPB Name"].astype("string").str.contains(
        r"2B|self[\s_-]*pickup", case=False, na=False
    )
    # Require all detail rows of an order to be packed before counting completion.
    grouped = frame.groupby("JD Order NO.", dropna=False)
    orders = grouped.agg(
        received=("Received Time", "min"),
        received_last=("Received Time", "max"),
        packed=("Packed Time", "max"),
        incomplete=("Packed Time", lambda values: values.isna().any()),
        excluded=("_excluded", "any"),
    )
    for label, column in STAGES.items():
        orders[label] = grouped[column].agg(lambda values: values.isna().any())
    if orders["received"].ne(orders["received_last"]).any():
        raise ValueError("One order has conflicting Received Time values.")
    left, right, deadline = backlog_window(day)
    if weekly:
        left, right = weekly_backlog_window(day)
    orders = orders.loc[orders.received.ge(left) & orders.received.lt(right) & ~orders.excluded]
    now = pd.Timestamp.now(TZ) if now is None else pd.Timestamp(now).tz_convert(TZ)
    complete = ~orders.incomplete & orders.packed.notna() & orders.packed.le(now)
    if weekly:
        total = len(orders)
        return {
            "Total received from last week": total,
            "Total backlog": int((~complete).sum()),
            "Completion rate": complete.sum() / total if total else None,
        }
    on_time = complete & orders.packed.le(deadline)
    late = complete & orders.packed.gt(deadline)
    total = len(orders)
    return {
        "Received orders": total, "Packed": int(complete.sum()),
        **{label: int(orders[label].sum()) for label in STAGES},
        "On-time packed": int(on_time.sum()), "Late packed": int(late.sum()),
        "Overdue backlog": int((~complete).sum()) if now > deadline else 0,
        "On-time completion": on_time.sum() / total if total else None,
    }
