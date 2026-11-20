"""Export both reports for one warehouse with a dedicated Chrome profile."""
import argparse
import json
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urlsplit
from time import monotonic

from playwright.sync_api import Error, TimeoutError, sync_playwright

from zoneinfo import ZoneInfo
TIMEZONE = ZoneInfo("America/Los_Angeles")
from iwms_browser import ExportError, WAREHOUSE_IDS, query_report, save_export, select_warehouse, warehouse_frame
from iwms_excel import normalize_export
from iwms_download import download_report
import pandas as pd



def open_warehouse_picker(page):
    if any(frame.get_by_role("button", name="Select", exact=True).filter(visible=True).count()
           for frame in page.frames):
        return
    print("正在通过 Basic Data → Select Warehouse 切仓…", flush=True)
    search = page.get_by_placeholder("Enter Menu Name", exact=True)
    try:
        print("正在搜索选仓菜单 Select…", flush=True)
        search.fill("Select", timeout=10_000)
        entries = page.get_by_text("Select Warehouse", exact=True).filter(visible=True)
        entries.first.wait_for(state="visible", timeout=10_000)
        candidates = [(entry, entry.bounding_box()) for entry in entries.all()]
        candidates = [(entry, box) for entry, box in candidates if box is not None]
        if not candidates:
            raise ExportError("菜单搜索后未找到 Select Warehouse。")
        menu = min(candidates, key=lambda item: item[1]["x"])[0]
        menu.scroll_into_view_if_needed(timeout=5_000)
        print("正在点击搜索结果 Select Warehouse…", flush=True)
        menu.click(timeout=10_000)
    except Error:
        raise ExportError("无法搜索或点击 Select Warehouse。") from None
    finally:
        if search.is_visible():
            search.fill("", timeout=5_000)
    print("正在等待选仓页面的 Select 按钮…", flush=True)
    warehouse_frame(page, timeout_seconds=20)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url")
    parser.add_argument("--warehouse", choices=WAREHOUSE_IDS, default="LAX1")
    parser.add_argument("--date", type=date.fromisoformat)
    parser.add_argument("--output-dir", type=Path, default=Path(__file__).with_name("iwms-exports"))
    parser.add_argument("--strict", action="store_true", help="Fail if any manual interaction is needed.")
    parser.add_argument("--account-menu-selector", help="Verified account-menu selector for checking a restored warehouse.")
    parser.add_argument("--click-login", action="store_true", help="Click Login on the company SSO landing page.")
    parser.add_argument("--login-only", action="store_true", help="Initialize the warehouse Chrome profile without exporting.")
    parser.add_argument("--profile-warehouse", choices=WAREHOUSE_IDS, help="Reuse an existing warehouse Chrome profile.")
    parser.add_argument("--all-warehouses", action="store_true", help="Switch warehouses through Basic Data in one Chrome session.")
    parser.add_argument("--workflows", nargs="+", choices=["picking", "packing"], default=["picking", "packing"])
    args = parser.parse_args()
    if args.login_only and args.strict:
        parser.error("--login-only requires manual SSO and cannot be combined with --strict.")
    if not args.url.startswith("https://") or any(char in args.url for char in "[]"):
        parser.error("请输入纯 HTTPS 网址，不要包含 Markdown 链接符号。")
    day = args.date or datetime.now(TIMEZONE).date()
    profile = Path(__file__).resolve().parent / ".runtime" / "iwms-chrome" / (args.profile_warehouse or args.warehouse)
    profile.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        with sync_playwright() as playwright:
            context = playwright.chromium.launch_persistent_context(
                profile, channel="chrome", headless=False, accept_downloads=True
            )
            try:
                page = context.pages[0] if context.pages else context.new_page()
                page.goto(args.url, wait_until="domcontentloaded")
                if args.click_login:
                    print("正在等待 iWMS 或公司 Login 入口…", flush=True)
                    deadline = monotonic() + 60
                    while monotonic() < deadline:
                        picker_ready = page.get_by_role("button", name="Select", exact=True)
                        report_ready = page.get_by_text("Report", exact=True)
                        if picker_ready.is_visible() or report_ready.is_visible():
                            break
                        login = page.get_by_role("button", name="Login", exact=True).or_(
                            page.get_by_role("link", name="Login", exact=True)
                        ).filter(visible=True)
                        if urlsplit(page.url).hostname == "pulsar-login.jd.com" and login.count() == 1:
                            print("已看到 Login，正在点击…", flush=True)
                            login.click(timeout=8_000)
                            print("Login 点击已完成。", flush=True)
                            break
                        page.wait_for_timeout(500)
                if not args.strict:
                    input(
                        "如果 Chrome 已进入 iWMS，可以直接按回车。"
                        "如果显示登录页，请手动完成 SSO 后按回车。"
                    )
                picker = page.get_by_role("button", name="Select", exact=True)
                report = page.get_by_text("Report", exact=True)
                print("正在等待 SSO 跳转到 iWMS（最多 60 秒）…", flush=True)
                try:
                    picker.or_(report).first.wait_for(state="visible", timeout=60_000)
                except TimeoutError:
                    login = page.get_by_role("button", name="Login", exact=True).or_(
                        page.get_by_role("link", name="Login", exact=True)
                    )
                    if (args.click_login
                            and urlsplit(page.url).hostname == "pulsar-login.jd.com"
                            and login.count() == 1 and login.is_visible()):
                        print("仍在 SSO 登录入口，正在重试一次 Login…", flush=True)
                        login.click(timeout=8_000)
                        try:
                            picker.or_(report).first.wait_for(state="visible", timeout=60_000)
                        except TimeoutError:
                            raise ExportError("重试 Login 后仍未进入 iWMS，可能需要手动完成公司 SSO。") from None
                    else:
                        raise ExportError("SSO 等待超时。请检查 Chrome 是否要求重新登录或验证。") from None
                if args.login_only:
                    print(f"{args.warehouse}: iWMS 已打开，Chrome 登录会话已保存到专用资料目录。")
                    return 0
                failures = 0
                warehouses = list(WAREHOUSE_IDS) if args.all_warehouses else [args.warehouse]
                for warehouse in warehouses:
                    print(f"正在刷新 {warehouse} · {day}…", flush=True)
                    try:
                        open_warehouse_picker(page)
                        select_warehouse(page, warehouse, interactive=not args.strict)
                    except (Error, ExportError) as error:
                        failures += 1
                        print(str(error) if isinstance(error, ExportError)
                              else f"{warehouse}: 切仓失败，已跳过该仓。", flush=True)
                        continue
                    for workflow in args.workflows:
                        print(f"正在处理 {warehouse} {workflow} {day}…", flush=True)
                        path = None
                        try:
                            frame = query_report(page, workflow, day, interactive=not args.strict)
                            download = download_report(page, frame, workflow, TIMEZONE)
                            path = save_export(download, args.output_dir)
                            normalize_export(path, workflow, warehouse)
                            snapshot = path.with_name(f"{warehouse}_{workflow}_{day}{path.suffix}")
                            path.replace(snapshot)
                            print(f"Dashboard snapshot saved as {snapshot.name}.", flush=True)
                        except (Error, AssertionError, ExportError, OSError) as error:
                            failures += 1
                            if path is not None:
                                path.unlink(missing_ok=True)
                            print(str(error) if isinstance(error, ExportError) else "报表操作失败。", flush=True)
                if args.strict and not failures:
                    print("PASS: All four warehouses exported Picking and Packing." if args.all_warehouses
                          else "PASS: Picking and Packing exported with no manual interaction.", flush=True)
                return int(bool(failures))
            finally:
                context.close()
    except (Error, EOFError, OSError, ExportError) as error:
        message = str(error) if isinstance(error, ExportError) else "请检查 Chrome、登录状态及本地目录。"
        parser.exit(1, f"固定仓库测试已停止。{message}\n")


if __name__ == "__main__":
    raise SystemExit(main())
