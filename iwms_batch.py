"""Run scheduled iWMS exports in one manually authenticated Chrome session."""
import argparse
from datetime import date, datetime
from functools import partial
from pathlib import Path
import re
from time import monotonic, sleep
from zoneinfo import ZoneInfo

from playwright.sync_api import Error, sync_playwright

from iwms_browser import ExportError, WAREHOUSE_IDS, prepare_report, save_export
from iwms_excel import normalize_export

TIMEZONE = ZoneInfo("America/Los_Angeles")


def open_account_menu(page, selector, interactive, *, timeout=60_000):
    marker = page.get_by_text("Sign Out", exact=True)
    if marker.is_visible():
        return
    if selector:
        page.locator(selector).hover(timeout=5_000)
        marker.wait_for(state="visible", timeout=5_000)
    elif interactive:
        print("请把鼠标停在 Chrome 右上角的头像上，直到脚本继续。", flush=True)
        marker.wait_for(state="visible", timeout=timeout)
    else:
        raise ExportError("定时运行需要已验证的账号菜单定位器。")


def warehouse_entry(page):
    return page.get_by_text(re.compile(
        "^(" + "|".join(re.escape(code) for code in WAREHOUSE_IDS.values()) + ")$"
    ))


def verify_warehouse(page, warehouse, *, selector, interactive):
    print("正在核对当前仓库编码…", flush=True)
    try:
        open_account_menu(page, selector, interactive, timeout=12_000)
        entry = warehouse_entry(page)
        entry.wait_for(state="visible", timeout=3_000)
        actual = entry.inner_text(timeout=3_000).strip()
    except Error:
        if not interactive:
            raise ExportError("无法读取账号菜单中的仓库编码。") from None
        actual = input(
            "请查看 Chrome 账号菜单里的当前仓库编码，然后在这里输入完整编码并按回车。"
            "如果看不到编码，直接按回车跳过。\n"
        ).strip().upper()
    if actual != WAREHOUSE_IDS[warehouse]:
        raise ExportError("当前仓库编码与目标不一致，已停止导出。")


def show_warehouse_picker(page, selector, interactive):
    picker = page.get_by_role("button", name="Select", exact=True)
    if picker.is_visible():
        return
    print("正在返回选仓页…", flush=True)
    if interactive and not selector:
        print("请在 Chrome 里悬停头像，然后直接点击账号菜单里的当前仓库编码。", flush=True)
        print("看到 Select Warehouse 后请停在那里，脚本会继续，无需按回车。", flush=True)
        timeout = 60_000
    else:
        open_account_menu(page, selector, interactive)
        try:
            warehouse_entry(page).click(timeout=5_000)
        except Error:
            raise ExportError("无法点击账号菜单中的仓库编码。") from None
        timeout = 10_000
    try:
        picker.wait_for(state="visible", timeout=timeout)
    except Error:
        raise ExportError("等待 Select Warehouse 页面超时。") from None


def run_batch(page, args):
    day = args.date or datetime.now(TIMEZONE).date()
    interactive = args.repeat_minutes is None
    verify = partial(verify_warehouse, selector=args.account_menu_selector, interactive=interactive)
    failures = 0
    for warehouse in args.warehouses:
        for workflow in args.workflows:
            print(f"{warehouse} {workflow} {day}", flush=True)
            try:
                show_warehouse_picker(page, args.account_menu_selector, interactive)
            except (Error, ExportError) as error:
                reason = str(error) if isinstance(error, ExportError) else "无法打开账号菜单或选仓页。"
                print(f"切换到 {warehouse} 之前停止。{reason}", flush=True)
                if interactive and not page.is_closed():
                    input("Chrome 会留在出错的页面。查看后回终端按回车结束。")
                raise ExportError(reason) from None
            path = None
            try:
                frame = prepare_report(page, warehouse, workflow, day,
                                       interactive=interactive, verify_warehouse=verify)
                with page.expect_download(timeout=120_000) as download:
                    frame.get_by_role("button", name="Export", exact=True).click(timeout=10_000)
                path = save_export(download.value, args.output_dir)
                normalize_export(path, workflow, warehouse)
                snapshot = path.with_name(f"{warehouse}_{workflow}_{day}{path.suffix}")
                path.replace(snapshot)
                print(f"Dashboard snapshot saved as {snapshot.name}.", flush=True)
            except (Error, AssertionError, ExportError, OSError) as error:
                failures += 1
                if path is not None:
                    path.unlink(missing_ok=True)
                reason = str(error) if isinstance(error, ExportError) else "界面、会话或本地文件操作失败。"
                print(f"已跳过此报表。{reason}", flush=True)
                if page.is_closed() or not (
                    page.get_by_text("Report", exact=True).is_visible()
                    or page.get_by_role("button", name="Select", exact=True).is_visible()
                ):
                    raise ExportError("iWMS 页面不可用。请重新登录后启动批量脚本。") from None
    return failures


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url")
    parser.add_argument("--warehouses", nargs="+", choices=WAREHOUSE_IDS, default=list(WAREHOUSE_IDS))
    parser.add_argument("--workflows", nargs="+", choices=["picking", "packing"], default=["picking", "packing"])
    parser.add_argument("--date", type=date.fromisoformat, help="Defaults to the current Los Angeles date each round.")
    parser.add_argument("--output-dir", type=Path, default=Path(__file__).with_name("iwms-exports"))
    parser.add_argument("--repeat-minutes", type=int, help="Run immediately, then repeat at this interval.")
    parser.add_argument("--account-menu-selector", help="A selector verified on the actual iWMS account-menu trigger.")
    args = parser.parse_args()
    if args.repeat_minutes is not None and (args.repeat_minutes < 1 or not args.account_menu_selector):
        parser.error("--repeat-minutes requires a positive interval and --account-menu-selector.")

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel="chrome", headless=False)
            page = browser.new_page(accept_downloads=True)
            page.goto(args.url, wait_until="domcontentloaded")
            input("请手动登录并保持 English。看到 iWMS 页面后，回终端按回车。")
            while True:
                started = monotonic()
                failures = run_batch(page, args)
                if args.repeat_minutes is None:
                    return int(bool(failures))
                print(f"本轮完成，跳过 {failures} 份。", flush=True)
                sleep(max(0, args.repeat_minutes * 60 - (monotonic() - started)))
    except KeyboardInterrupt:
        return 130
    except ExportError as error:
        parser.exit(1, f"批量任务已停止。{error}\n")
    except (Error, EOFError):
        parser.exit(1, "批量任务已停止。请检查 iWMS 登录状态和页面控件后重跑。")


if __name__ == "__main__":
    raise SystemExit(main())
