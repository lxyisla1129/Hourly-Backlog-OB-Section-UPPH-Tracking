"""Capture an authorized Excel export and read it for iwms.normalize."""
import argparse
from datetime import date
import re
from time import monotonic, sleep
from pathlib import Path
from uuid import uuid4

import pandas as pd
from iwms_calendar import backlog_window, weekly_backlog_window
from playwright.sync_api import Error, TimeoutError, expect, sync_playwright


class ExportError(Exception):
    """A capture or workbook error safe to display without browser details."""


def save_export(download, directory):
    suffix = Path(download.suggested_filename).suffix.lower()
    if suffix not in {".xlsx", ".xls"}:
        raise ExportError("The downloaded file did not have an Excel extension.")
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{uuid4().hex}{suffix}"
    try:
        download.save_as(path)
    except Error:
        path.unlink(missing_ok=True)
        raise ExportError("Chrome could not save the Excel download.") from None
    return path


def read_export(path, *, sheet=0, header=0, required_columns=()):
    """Return Excel rows keyed by their exact headers, for iwms.normalize."""
    try:
        frame = pd.read_excel(path, sheet_name=sheet, header=header, dtype=object)
    except Exception:
        raise ExportError("Could not read the workbook. Check the file, sheet and header row.") from None
    if set(required_columns).difference(frame.columns):
        raise ExportError("Workbook headers do not match the selected iWMS report.")
    return frame.dropna(how="all").to_dict(orient="records")


WAREHOUSE_IDS = {
    "LAX1": "C0000000578",
    "LAX2": "C0000000579",
    "LAX4": "C0000008149",
    "LAX5": "C0000009307",
}
REPORT_NAMES = {"backlog": "Sales Outbound Comprehensive Query", "picking": "Picking Results Query", "packing": "Sales Order Review Result Inquiry"}
DATE_LABELS = {"backlog": "Receive Date", "picking": "Picking Date", "packing": "Recheck Time"}
DATE_TIME = re.compile(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}")
DATE_PART = re.compile(r"(?:\d{4}-\d{2}-\d{2}|\d{2}:\d{2}:\d{2})")


def visible_input_group(page, pattern, count):
    inputs = []
    for field in page.get_by_role("textbox").all():
        if field.is_visible() and pattern.fullmatch(field.input_value()):
            inputs.append(field)
    if len(inputs) != count:
        raise ExportError("Could not identify the report date fields uniquely.")
    return inputs


def warehouse_frame(page, timeout_seconds=15):
    deadline = monotonic() + timeout_seconds
    while monotonic() < deadline:
        matches = [
            frame for frame in page.frames
            if frame.get_by_role("button", name="Select", exact=True).filter(visible=True).count() == 1
        ]
        if len(matches) == 1:
            return matches[0]
        page.wait_for_timeout(250)
    raise ExportError("未在主页面或内嵌页面找到唯一的选仓 Select 按钮。")


def select_warehouse(page, warehouse, *, interactive=True, verify_warehouse=None):
    scope = warehouse_frame(page)
    print("正在打开仓库下拉框…", flush=True)
    warehouse_text = re.compile(r"\(" + re.escape(WAREHOUSE_IDS[warehouse]) + r"\)$")
    option = scope.get_by_text(warehouse_text).filter(visible=True)
    if not option.is_visible():
        try:
            scope.get_by_placeholder("Select", exact=True).click(timeout=4_000)
        except Error:
            if not interactive:
                raise
            input("请在 Chrome 点开仓库下拉框，保持列表展开，然后回终端按回车。")
    print("正在选择目标仓库…", flush=True)
    option.click(timeout=10_000)
    print("正在点击 Select 确认仓库…", flush=True)
    report_management = page.get_by_text("Report", exact=True)
    select_button = scope.get_by_role("button", name="Select", exact=True)
    if select_button.is_visible() or not report_management.is_visible():
        try:
            select_button.click(timeout=5_000)
        except Error:
            if not interactive:
                raise
            input("请在 Chrome 点击 Select。看到顶部Report后，回终端按回车。")
    print("正在等待主界面并打开报表管理…", flush=True)
    select_button.wait_for(state="hidden", timeout=20_000)
    report_management.wait_for(state="visible", timeout=20_000)
    if verify_warehouse is not None:
        verify_warehouse(page, warehouse)

def prepare_report(page, warehouse, workflow, day, *, interactive=True, verify_warehouse=None):
    select_warehouse(page, warehouse, interactive=interactive, verify_warehouse=verify_warehouse)
    return query_report(page, workflow, day, interactive=interactive)


def query_report(page, workflow, day, *, interactive=True):
    report_management = page.get_by_text("Report", exact=True)
    report_management.click(timeout=20_000)
    print("正在打开目标报表…", flush=True)
    search = page.get_by_placeholder("Enter Menu Name", exact=True)
    try:
        print(f"正在搜索报表菜单 {REPORT_NAMES[workflow]}…", flush=True)
        search.fill(REPORT_NAMES[workflow], timeout=10_000)
        matches = page.get_by_text(REPORT_NAMES[workflow], exact=True).filter(visible=True)
        matches.first.wait_for(state="visible", timeout=10_000)
        candidates = [(item, item.bounding_box()) for item in matches.all()]
        candidates = [(item, box) for item, box in candidates if box is not None]
        if not candidates:
            raise ExportError("未找到目标报表的菜单搜索结果。")
        # Sidebar search result sits left of existing report tabs.
        menu = min(candidates, key=lambda item: item[1]["x"])[0]
        menu.scroll_into_view_if_needed(timeout=5_000)
        menu.click(timeout=10_000)
    except Error:
        raise ExportError(f"无法从菜单搜索打开 {REPORT_NAMES[workflow]}。") from None
    finally:
        if search.is_visible():
            search.fill("", timeout=5_000)
    day_text = day.isoformat()
    print("正在定位报表内容…", flush=True)
    try:
        report_frame = find_report_frame(page, workflow)
        set_report_date(report_frame, day, backlog=workflow == "backlog")
    except (Error, AssertionError, ExportError) as error:
        if isinstance(error, ExportError):
            print(str(error), flush=True)
        if not interactive:
            raise
        input(
            f"请在 Chrome 选好 {day_text} 全天。分别确认开始和结束时间的小 OK，"
            "再点击外层 OK。确认日历已关闭后，回终端按回车。"
        )
    print("正在核对 OK 后页面显示的日期…", flush=True)
    report_frame = find_report_frame(page, workflow)
    report_frame.get_by_text("OK", exact=True).filter(visible=True).first.wait_for(
        state="hidden", timeout=5_000
    )
    start, end = visible_input_group(report_frame, DATE_TIME, 2)
    if (start.input_value(), end.input_value()) != report_range(day, workflow == "backlog"):
        raise ExportError("页面日期与命令指定日期不一致，已停止查询。")
    print("正在点击 Query 并等待结果…", flush=True)
    try:
        report_frame.get_by_role("button", name="Query", exact=True).filter(visible=True).click(timeout=10_000)
    except Error:
        raise ExportError("已核对日期，但无法点击报表里的 Query。") from None
    # The pagination starts at Total 0 on a freshly opened report.
    result_total = report_frame.get_by_text(re.compile(r"^Total\s+[1-9][\d,]*$"))
    try:
        result_total.filter(visible=True).first.wait_for(state="visible", timeout=45_000)
    except TimeoutError:
        raise ExportError("未看到非空查询结果。报表可能为空，或查询尚未完成。") from None
    print("报表已显示查询结果。", flush=True)
    return report_frame


def find_report_frame(page, workflow):
    label = re.compile(r"^" + re.escape(DATE_LABELS[workflow]) + r"\s*[:：]?$")
    deadline = monotonic() + 20
    while monotonic() < deadline:
        matches = [
            frame for frame in page.frames
            if any(item.is_visible() for item in frame.get_by_text(label).all())
        ]
        if len(matches) == 1:
            return matches[0]
        sleep(0.2)
    raise ExportError(
        f"找不到唯一的报表日期标签 {DATE_LABELS[workflow]}。"
        "请核对当前英文报表标题和日期栏标签。"
    )


def report_range(day, backlog=False):
    if backlog:
        left, right = weekly_backlog_window(day)
        return left.strftime("%Y-%m-%d %H:%M:%S"), (right - pd.Timedelta(seconds=1)).strftime("%Y-%m-%d %H:%M:%S")
    return f"{day} 00:00:00", f"{day} 23:59:59"


def set_report_date(page, day, *, backlog=False):
    print("正在定位日期范围输入框…", flush=True)
    start, end = visible_input_group(page, DATE_TIME, 2)
    day_text = day.isoformat()
    target_start, target_end = report_range(day, backlog)
    confirm = page.get_by_text("OK", exact=True).filter(visible=True)
    if not confirm.count():
        if (start.input_value(), end.input_value()) == (target_start, target_end):
            return
        print("正在打开日期范围弹窗…", flush=True)
        start.click(timeout=5_000)
        confirm.first.wait_for(state="visible", timeout=5_000)
    print("正在定位弹出的日期和时间输入框…", flush=True)
    fields = visible_input_group(page, DATE_PART, 4)
    # Set the end first for a future target, so the range stays ordered.
    dates = [(0, target_start.split()[0]), (2, target_end.split()[0])]
    if day_text > end.input_value().split()[0]:
        dates.reverse()
    print("正在填写日期和时间…", flush=True)
    for index, value in dates:
        print(f"正在设置{'开始' if index == 0 else '结束'}日期…", flush=True)
        if fields[index].input_value() != value:
            fields[index].fill(value, timeout=5_000)
            # Commit on blur without tabbing into the adjacent time picker.
            fields[index].evaluate("(input) => input.blur()", timeout=5_000)
            expect(fields[index]).to_have_value(value, timeout=5_000)

    ok_text = confirm
    cancel = page.get_by_text("Cancel", exact=True).filter(visible=True)
    # Close a pre-opened time picker before selecting the intended endpoint.
    if cancel.is_visible():
        print("正在关闭已打开的时间小弹窗…", flush=True)
        cancel.click(timeout=5_000)
        cancel.wait_for(state="hidden", timeout=5_000)
    for index, value in [(1, target_start.split()[1]), (3, target_end.split()[1])]:
        label = "开始" if index == 1 else "结束"
        print(f"正在打开{label}时间小弹窗…", flush=True)
        fields[index].focus(timeout=5_000)
        if not cancel.is_visible():
            fields[index].click(timeout=5_000)
        try:
            cancel.wait_for(state="visible", timeout=5_000)
        except TimeoutError:
            raise ExportError(f"未看到{label}时间小弹窗里的 Cancel。") from None
        if fields[index].input_value() != value:
            fields[index].fill(value, timeout=5_000)
        buttons = [(button, button.bounding_box(timeout=3_000))
                   for button in ok_text.all() if button.is_visible()]
        if len(buttons) != 2 or any(box is None for _, box in buttons):
            raise ExportError("无法区分时间小弹窗和日期范围的 OK。")
        # The time popup's OK is above the range footer in the observed layout.
        inner, outer = sorted(buttons, key=lambda item: item[1]["y"])
        if inner[1]["y"] >= outer[1]["y"]:
            raise ExportError("无法确认时间小弹窗的 OK。")
        print(f"正在确认{label}时间的小 OK…", flush=True)
        inner[0].click(timeout=5_000)
        cancel.wait_for(state="hidden", timeout=5_000)
        expect(fields[index]).to_have_value(value, timeout=5_000)
    print("正在点击日期范围的外层 OK…", flush=True)
    confirm.click(timeout=5_000)
    print("正在核对页面显示的日期范围…", flush=True)
    expect(start).to_have_value(target_start)
    expect(end).to_have_value(target_end)


def capture(url, directory, timeout_seconds, *, click_export=False, selection_args=None):
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel="chrome", headless=False)
            try:
                context = browser.new_context(accept_downloads=True)
                page = context.new_page()
                page.goto(url, wait_until="domcontentloaded")
                if selection_args:
                    input("手动登录 iWMS。看到仓库选择页面后，切回终端按回车。")
                else:
                    input(
                        "Complete SSO in Chrome and select the warehouse, report and dates. "
                        "With the Export button visible, press Enter here. "
                    )
                # Select the actual report tab if navigation opened another tab.
                pages = context.pages
                if len(pages) > 1:
                    for index, tab in enumerate(pages, start=1):
                        print(f"Chrome tab {index}")
                    selection = input("Enter the report tab number in Chrome tab order: ")
                    if not selection.isdecimal() or not 1 <= int(selection) <= len(pages):
                        raise ExportError("Invalid report tab number.")
                    page = pages[int(selection) - 1]
                report_frame = page
                if selection_args:
                    try:
                        report_frame = prepare_report(page, *selection_args)
                    except (Error, AssertionError):
                        raise ExportError(
                            "无法确认仓库、报表或日期控件，已停止导出。"
                            "终端最后一条「正在…」消息标明了停在哪一步。"
                        ) from None
                with page.expect_download(timeout=timeout_seconds * 1000) as pending:
                    if click_export:
                        print("正在点击 Export…", flush=True)
                        button = report_frame.get_by_role("button", name=re.compile(r"^(Export|导出)$"), exact=True)
                        try:
                            button.click(timeout=10_000)
                        except Error:
                            raise ExportError(
                                "Could not click the Export button. Check the active report tab "
                                "and sign-in status, or run without --click-export."
                            ) from None
                    else:
                        print("Click Export in Chrome now.", flush=True)
                return save_export(pending.value, directory)
            finally:
                browser.close()
    except TimeoutError:
        raise ExportError(
            "Chrome timed out loading the site or waiting for the download. "
            "Check that the session is signed in and that Export starts a file download."
        ) from None
    except Error:
        raise ExportError("Chrome could not complete the capture. Check Chrome and the site URL.") from None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url", help="Approved iWMS sign-in URL")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--timeout", type=int, default=120, help="Download wait in seconds")
    parser.add_argument("--click-export", action="store_true", help="Click the visible Export button")
    parser.add_argument("--warehouse", choices=WAREHOUSE_IDS)
    parser.add_argument("--workflow", choices=REPORT_NAMES)
    parser.add_argument("--date", type=date.fromisoformat)
    parser.add_argument("--sheet-index", type=int, default=0)
    parser.add_argument("--header-row", type=int, default=0, help="Zero-based header row")
    args = parser.parse_args()
    if args.timeout <= 0 or args.sheet_index < 0 or args.header_row < 0:
        parser.error("Use a positive timeout and nonnegative sheet and header indices.")
    selection_args = (args.warehouse, args.workflow, args.date)
    if any(selection_args) and not all(selection_args):
        parser.error("--warehouse, --workflow, and --date must be provided together.")
    automatic = all(selection_args)
    try:
        path = capture(
            args.url, args.output_dir, args.timeout,
            click_export=args.click_export or automatic,
            selection_args=selection_args if automatic else None,
        )
        print(f"Saved Excel export as {path.name}.")
        rows = read_export(path, sheet=args.sheet_index, header=args.header_row)
        if automatic and args.sheet_index == 0 and args.header_row == 0:
            from iwms_excel import REPORT_COLUMNS

            read_export(path, required_columns=REPORT_COLUMNS[args.workflow].values())
            snapshot = path.with_name(f"{args.warehouse}_{args.workflow}_{args.date}{path.suffix}")
            path.replace(snapshot)
            print(f"Dashboard snapshot saved as {snapshot.name}.")
    except (ExportError, OSError, EOFError) as error:
        parser.exit(1, f"{error if isinstance(error, ExportError) else 'Could not complete local capture.'}\n")
    print("Workbook read successfully." if rows else "Workbook has no data rows.")


if __name__ == "__main__":
    main()
