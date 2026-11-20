"""Streamlit dashboard for locally exported iWMS workbooks."""
from datetime import datetime
import json
from time import time
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

from iwms_browser import ExportError, WAREHOUSE_IDS
from iwms_excel import normalize_export
from metrics import cumulative, employee_summary, hourly, week_to_date
from iwms_segments import special_business
from iwms_backlog import backlog_summary
from iwms_calendar import backlog_window, weekly_backlog_window

title = "Hourly Backlog & OB Section UPPH Tracking"
st.set_page_config(page_title=title, layout="wide")
st.title(title)
st.caption("Activity-span UPPH. Packing uses sales order recheck quantities and timestamps.")

with st.sidebar:
    warehouse = st.selectbox("Warehouse for these exports", WAREHOUSE_IDS)
    day = st.date_input("Date", datetime.now(ZoneInfo("America/Los_Angeles")).date())

@st.fragment(run_every=5)
def refresh_controls():
    state_dir = Path(__file__).resolve().parent / ".runtime" / "iwms-refresh"
    state_dir.mkdir(parents=True, exist_ok=True)
    state_path = state_dir / "status.json"
    state = json.loads(state_path.read_text()) if state_path.exists() else {}
    request = state_dir / "requested"
    cooling = time() - state.get("started", 0) < 1800
    with st.sidebar:
        if st.button(f"Refresh {warehouse} data", disabled=cooling or request.exists()):
            try:
                with request.open("x") as pending:
                    json.dump({"warehouse": warehouse, "date": str(day)}, pending)
            except FileExistsError:
                pass
            st.rerun(scope="fragment")
        st.caption("Refreshes the selected warehouse and date. Shared 30-minute limit.")
        if state.get("running"):
            st.info(f"Collecting {state.get('warehouse', 'warehouse data')} · {state.get('date', '')}. Large exports may take several minutes.")
        if request.exists():
            st.info("Refresh queued. Keep the collector running.")
        if state.get("finished"):
            updated = datetime.fromtimestamp(state["finished"], ZoneInfo("America/Los_Angeles"))
            st.caption(f"Last round: {updated:%m-%d %H:%M} · {'Complete' if state.get('success') else 'Partial failure'}")


refresh_controls()

@st.fragment(run_every=60)
def packing_backlog():
    directory = Path(__file__).with_name("iwms-exports")
    files = list(directory.glob(f"{warehouse}_backlog_{day}.xlsx")) + list(
        directory.glob(f"{warehouse}_backlog_{day}.xls")
    )
    if not files:
        st.info(f"{warehouse} · {day}: waiting for the first automatic backlog export.")
        st.caption("Keep iwms_refresh.py running. Backlog is collected after Picking and Packing.")
        return
    export = max(files, key=lambda path: path.stat().st_mtime_ns)
    updated = datetime.fromtimestamp(export.stat().st_mtime, ZoneInfo("America/Los_Angeles"))
    st.caption(f"Automatic export updated {updated:%Y-%m-%d %H:%M} · Los Angeles time")
    try:
        result = backlog_summary(pd.read_excel(export, dtype=object), day)
    except (ValueError, OSError):
        st.error("Cannot verify this export's required columns, order IDs or timestamps.")
        return
    summary_tab, processing_tab = st.tabs(["Backlog Summary", "Total Processing Status"])
    with summary_tab:
        st.subheader("Backlog Summary")
        coverage = export.with_suffix(".coverage.json")
        if coverage.exists():
            weekly_result = backlog_summary(pd.read_excel(export, dtype=object), day, weekly=True)
            weekly_columns = st.columns(3)
            for column, (label, value) in zip(weekly_columns, weekly_result.items()):
                text = f"{value:.2%}" if label == "Completion rate" and value is not None else ("—" if value is None else f"{value:,}")
                column.metric(label, text)
            week_left, week_right = weekly_backlog_window(day)
            st.caption(f"Receipt cohorts for the selected date and preceding six dates, including weekend rollover: {week_left:%m-%d %H:%M} to {week_right:%m-%d %H:%M} (exclusive). Status as of the export update time above. Excludes 2B/selfpickup.")
        else:
            st.info("Weekly totals await a new weekly-range backlog export; this older file only covers the daily cohort.")
    with processing_tab:
        st.subheader("Total Processing Status")
        left, right, deadline = backlog_window(day)
        st.caption(f"{warehouse} · Received {left:%m-%d %H:%M} to {right:%m-%d %H:%M} (exclusive) · Deadline {deadline:%m-%d %H:%M} · Los Angeles time")
        st.caption("BL is a reference based on a 24h baseline. Weekend receipts from Friday 15:00 to Monday 15:00 are due Monday 17:30. Pending stages are counted independently and may overlap.")
        daily_labels = {
            "Received orders": "Received orders",
            "Packed": "Packed",
            "Pending picking": "Pending picking",
            "Pending recheck": "Pending recheck",
            "Pending packing": "Pending pack",
            "Pending handover": "Pending handover",
            "On-time completion": "On-time completion",
        }
        columns = st.columns(3)
        for index, (key, label) in enumerate(daily_labels.items()):
            value = result[key]
            text = f"{value:.2%}" if key == "On-time completion" and value is not None else ("—" if value is None else f"{value:,}")
            columns[index % 3].metric(label, text)


packing_backlog()


@st.fragment(run_every=60)
def dashboard():
    directory = Path(__file__).with_name("iwms-exports")
    week_start = pd.Timestamp(day) - pd.Timedelta(days=pd.Timestamp(day).weekday())
    frames = []
    missing = []
    try:
        for current in pd.date_range(week_start, pd.Timestamp(day)):
            for workflow in ("picking", "packing"):
                files = [p for p in directory.glob(f"{warehouse}_{workflow}_{current.date()}.*")
                         if p.suffix in {".xlsx", ".xls"}]
                source = max(files, key=lambda p: p.stat().st_mtime_ns) if files else None
                if source is None:
                    missing.append(f"{current:%m-%d} {workflow}")
                    continue
                frame = normalize_export(source, workflow, warehouse)
                frame = frame.loc[frame.end_time.dt.date.eq(current.date())]
                frames.append(frame)
    except ExportError as error:
        st.error(str(error))
        return

    if not frames:
        available = [
            name for name in WAREHOUSE_IDS
            if any(directory.glob(f"{name}_*_{day}.xlsx"))
            or any(directory.glob(f"{name}_*_{day}.xls"))
        ]
        st.info(f"No saved exports for {warehouse} on {day}.")
        if available:
            st.info("Available warehouses for this date: " + ", ".join(available))
        else:
            st.info("No warehouse exports are available for this date yet.")
        return

    df = pd.concat(frames, ignore_index=True)
    special_label, special_mask = special_business(warehouse, df["customer"])
    if special_label:
        business = st.radio(
            "Business", ["Normal UPPH", special_label], horizontal=True,
            key=f"business_{warehouse}",
        )
        df = df.loc[special_mask if business == special_label else ~special_mask].copy()
    else:
        business = "Normal UPPH"
    for key, label in (("customer", "Customer"), ("employee", "Employee")):
        options = sorted(df[key].dropna().unique().tolist())
        selected = st.multiselect(label, options, placeholder=f"All {label.lower()}s")
        if selected:
            df = df.loc[df[key].isin(selected)]

    st.subheader("WTD Picking & Packing UPPH")
    weekly = week_to_date(df).rename(columns={"uph": "upph"})
    st.dataframe(weekly.style.format({"units": "{:.2f}", "hours": "{:.2f}", "upph": "{:.2f}"}), hide_index=True)
    st.caption(f"Monday {week_start:%m-%d} through {day}. UPPH = total units / sum of daily activity hours.")
    if missing:
        st.warning("WTD has missing exports: " + ", ".join(missing))
    df = df.loc[df.end_time.dt.date.eq(day)]
    if df.empty:
        st.info("No activity matches the selected date and filters.")
        return

    summary = cumulative(df).rename(columns={"uph": "upph"})
    st.subheader(f"{business} · Daily cumulative" if special_label else "Daily cumulative UPPH")
    number_formats = {"hours": "{:.2f}", "units": "{:.2f}", "upph": "{:.2f}"}
    st.dataframe(summary.style.format(number_formats), hide_index=True)

    hours = hourly(df, day).rename(columns={"uph": "upph"})
    employees = employee_summary(df).rename(columns={"uph": "upph"})
    change = "UPPH change vs. prior hour"
    workflow_average = summary.set_index("workflow")["upph"]

    for workflow, tab in zip(("picking", "packing"), st.tabs(["Picking", "Packing"])):
        with tab:
            hourly_detail = hours.loc[hours["workflow"].eq(workflow)].drop(columns="workflow")
            if hourly_detail.empty:
                st.info(f"No {workflow} activity matches the current filters.")
                continue

            st.subheader("Hourly UPPH")
            st.line_chart(hourly_detail.set_index("hour")["upph"])
            previous = hourly_detail["upph"].shift()
            hourly_detail[change] = hourly_detail["upph"].div(previous.where(previous > 0)) - 1
            st.dataframe(
                hourly_detail.style.format({**number_formats, change: "{:+.1%}"}, na_rep="—").map(
                    lambda value: "color: #dc2626" if pd.notna(value) and value < 0 else "",
                    subset=[change],
                ),
                hide_index=True,
            )
            st.caption("Change compares consecutive hours.")

            st.subheader(f"{workflow.title()} employee productivity")
            baseline = workflow_average.loc[workflow]
            detail = employees.loc[employees["workflow"].eq(workflow)].drop(columns="workflow")
            detail["Workflow UPPH"] = baseline
            st.dataframe(
                detail.style.format({**number_formats, "Workflow UPPH": "{:.2f}"}, na_rep="—").map(
                    lambda value: "color: #dc2626" if pd.notna(value) and value < baseline else "",
                    subset=["upph"],
                ),
                hide_index=True,
            )
            st.caption("Red UPPH is below the workflow's cumulative UPPH for the current filters.")

    st.download_button("Download employee CSV", employees.to_csv(index=False).encode(),
                       file_name=f"{warehouse}_{business.replace(' ', '_')}_{day}.csv", mime="text/csv")


dashboard()
