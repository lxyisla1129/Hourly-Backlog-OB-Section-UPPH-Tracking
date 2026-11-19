"""Activity-span productivity calculations."""
import pandas as pd


def employee_summary(df):
    keys = ["warehouse", "workflow", "employee", "customer"]
    if df.empty:
        return pd.DataFrame(columns=[
            *keys, "units", "first_activity", "last_activity", "hours", "uph"
        ])
    out = df.groupby(keys, dropna=False).agg(
        units=("units", "sum"),
        first_activity=("start_time", "min"),
        last_activity=("end_time", "max"),
    ).reset_index()
    out["hours"] = (out.last_activity - out.first_activity).dt.total_seconds() / 3600
    out["uph"] = out.units.div(out.hours.where(out.hours > 0))
    return out


def cumulative(df):
    if df.empty:
        return pd.DataFrame(columns=["workflow", "units", "hours", "uph"])
    spans = df.groupby(["workflow", "employee"], dropna=False).agg(
        first=("start_time", "min"), last=("end_time", "max"), units=("units", "sum")
    ).reset_index()
    spans["hours"] = (spans["last"] - spans["first"]).dt.total_seconds() / 3600
    out = spans.groupby("workflow").agg(units=("units", "sum"), hours=("hours", "sum")).reset_index()
    out["uph"] = out.units.div(out.hours.where(out.hours > 0))
    return out


def hourly(df, day):
    columns = ["hour", "workflow", "units", "hours", "uph"]
    rows = []
    if df.empty:
        return pd.DataFrame(columns=columns)
    day = pd.Timestamp(day).normalize()
    for hour in range(24):
        left = day + pd.Timedelta(hours=hour)
        right = left + pd.Timedelta(hours=1)
        for workflow, group in df.groupby("workflow"):
            completed = group[(group.end_time >= left) & (group.end_time < right)]
            units = completed.units.sum()
            total_hours = 0.0
            for _, events in group.groupby("employee"):
                if workflow == "packing":
                    in_hour = events[(events.end_time >= left) & (events.end_time < right)]
                    if len(in_hour) >= 2:
                        total_hours += (in_hour.end_time.max() - in_hour.end_time.min()).total_seconds() / 3600
                else:
                    overlaps = events[(events.start_time < right) & (events.end_time > left)]
                    if not overlaps.empty:
                        begin = max(overlaps.start_time.min(), left)
                        finish = min(overlaps.end_time.max(), right)
                        total_hours += max((finish - begin).total_seconds() / 3600, 0)
            rows.append({
                "hour": left.strftime("%H:00"), "workflow": workflow, "units": float(units),
                "hours": total_hours, "uph": units / total_hours if total_hours > 0 else float("nan"),
            })
    return pd.DataFrame(rows, columns=columns)


def week_to_date(df):
    """Sum daily denominators to avoid counting overnight gaps as labor."""
    if df.empty:
        return pd.DataFrame(columns=["workflow", "units", "hours", "uph"])
    daily = [cumulative(events) for _, events in df.groupby(df.end_time.dt.date)]
    result = pd.concat(daily).groupby("workflow", as_index=False)[["units", "hours"]].sum()
    result["uph"] = result.units.div(result.hours.where(result.hours > 0))
    return result
