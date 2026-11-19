"""Follow the iWMS export task page when a report is queued."""
from datetime import datetime, timedelta
import re
from time import monotonic

from playwright.sync_api import Error

from iwms_browser import ExportError

TASK_TYPES = {
    "backlog": "Comprehensive Customer Order Query",
    "picking": "wms_picking_data_v2",
    "packing": "Customer Order Recheck Result Query",
}
TASK_ID = re.compile(r"^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$", re.I)


def new_task(frame, workflow, submitted):
    rows = frame.get_by_role("row").filter(
        has_text=re.compile(re.escape(TASK_TYPES[workflow]).replace(r"\ ", r"\s+"))
    )
    matches = set()
    for row in rows.all():
        cells = row.get_by_role("cell").all_text_contents()
        if len(cells) < 5 or not TASK_ID.fullmatch(cells[0].strip()):
            continue
        try:
            created = datetime.fromisoformat(cells[1].strip())
        except ValueError:
            continue
        if submitted - timedelta(seconds=2) <= created <= submitted + timedelta(seconds=45):
            matches.add(cells[0].strip())
    if len(matches) > 1:
        raise ExportError("找到多个近期同类型任务，无法安全确认本次导出。")
    return next(iter(matches), None)


def download_report(page, report, workflow, timezone, *, timeout_seconds=600):
    context = page.context
    downloads = []
    tracked = []

    def record_download(download):
        downloads.append(download)

    def track(tab):
        tab.on("download", record_download)
        tracked.append(tab)

    for tab in context.pages:
        track(tab)
    context.on("page", track)
    try:
        submitted = datetime.now(timezone).replace(tzinfo=None)
        print("正在点击 Export…", flush=True)
        try:
            report.get_by_text("Export", exact=True).filter(visible=True).click(timeout=10_000)
        except Error:
            raise ExportError("查询已有结果，但无法点击报表里的 Export。") from None
        print("正在等待直接下载或任务列表…", flush=True)
        deadline = monotonic() + timeout_seconds
        next_query = monotonic() + 10
        task_id = None
        task_page_seen = False
        while monotonic() < deadline:
            if downloads:
                return downloads[0]
            for tab in context.pages:
                for frame in tab.frames:
                    if not frame.get_by_text("Task NO.", exact=True).filter(visible=True).first.is_visible():
                        continue
                    if not frame.get_by_text("Task Status", exact=True).filter(visible=True).first.is_visible():
                        continue
                    if not task_page_seen:
                        print("导出已进入任务列表，正在等待本次任务完成…", flush=True)
                        task_page_seen = True
                    task_id = task_id or new_task(frame, workflow, submitted)
                    if task_id:
                        row = frame.get_by_role("row").filter(has_text=task_id)
                        completed = row.get_by_text("Execution Completed", exact=True)
                        if completed.is_visible():
                            link = row.get_by_text("Download", exact=True)
                            print("本次任务已完成，正在点击 Download…", flush=True)
                            try:
                                with tab.expect_download(timeout=60_000) as pending:
                                    link.click(timeout=10_000)
                                return pending.value
                            except Error:
                                raise ExportError("任务已完成，但点击 Download 后未收到文件。") from None
                    if monotonic() >= next_query:
                        try:
                            frame.get_by_role("button", name="Query", exact=True).filter(visible=True).click(timeout=5_000)
                        except Error:
                            raise ExportError("已找到任务列表，但无法点击该页的 Query 刷新。") from None
                        next_query = monotonic() + 10
            active = next((tab for tab in context.pages if not tab.is_closed()), None)
            if active is None:
                raise ExportError("Chrome 页面已关闭，无法继续等待导出。")
            active.wait_for_timeout(1_000)
        if task_page_seen and task_id is None:
            raise ExportError("未找到创建时间和报表类型匹配的新任务。")
        raise ExportError("等待 Excel 下载或导出任务完成超时。")
    finally:
        context.remove_listener("page", track)
        for tab in tracked:
            tab.remove_listener("download", record_download)
