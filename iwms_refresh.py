"""Refresh all four iWMS warehouses sequentially using one Chrome profile."""
import argparse
import portalocker
import json
from time import time
from datetime import date, datetime
from pathlib import Path
import subprocess
import sys
from time import monotonic, sleep

from iwms_batch import TIMEZONE
from iwms_browser import WAREHOUSE_IDS
from iwms_retention import cleanup


def refresh(url, day, account_menu_selector, warehouse=None):
    script = Path(__file__).with_name("iwms_fixed.py")
    command = [
        sys.executable, "-u", str(script), url,
        "--profile-warehouse", "LAX1", "--date", day.isoformat(),
        "--click-login", "--strict",
    ]
    if warehouse:
        command.extend(["--warehouse", warehouse, "--force-backlog"])
    else:
        command.append("--all-warehouses")
    result = subprocess.run(command, check=False)
    if result.returncode:
        print("本轮有报表未完成，请查看上方日志。", flush=True)
    return int(result.returncode != 0)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url")
    parser.add_argument("--date", type=date.fromisoformat, help="Default: current Los Angeles date each round.")
    parser.add_argument("--once", action="store_true", help="Test one round and exit.")
    parser.add_argument("--every-minutes", type=int, default=120)
    parser.add_argument("--account-menu-selector")
    args = parser.parse_args()
    if args.every_minutes < 1:
        parser.error("--every-minutes must be positive.")
    state_dir = Path(__file__).resolve().parent / ".runtime" / "iwms-refresh"
    state_dir.mkdir(parents=True, exist_ok=True)
    request = state_dir / "requested"
    state_path = state_dir / "status.json"
    lock = (state_dir / "worker.lock").open("a")
    try:
        portalocker.lock(lock, portalocker.LOCK_EX | portalocker.LOCK_NB)
    except portalocker.exceptions.LockException:
        parser.exit(1, "已有刷新任务在运行。\n")
    try:
        next_run = 0
        last_start = 0
        if state_path.exists():
            previous = json.loads(state_path.read_text())
            last_start = previous.get("started", 0)
            if previous.get("success") and not previous.get("running"):
                next_run = previous.get("automatic_started", last_start) + args.every_minutes * 60
            else:
                print("上轮未成功完成，立即重新采集。", flush=True)
        if next_run > time():
            next_time = datetime.fromtimestamp(next_run, TIMEZONE)
            print(
                f"刷新进程已启动。上轮已结束，下一轮预计 {next_time:%Y-%m-%d %H:%M:%S}（洛杉矶时间）。",
                flush=True,
            )
            print("保持此终端打开；可通过页面 Refresh warehouse data 请求刷新（间隔至少 30 分钟）。", flush=True)
        else:
            print("刷新进程已启动，准备运行四个仓库。", flush=True)
        while True:
            now = time()
            requested = request.exists()
            if now >= next_run or (requested and now - last_start >= 1800):
                today = datetime.now(TIMEZONE).date()
                target_warehouse = None
                target_day = args.date or today
                manual = requested and now - last_start >= 1800
                if manual:
                    try:
                        payload = json.loads(request.read_text())
                        target_warehouse = payload["warehouse"]
                        target_day = date.fromisoformat(payload["date"])
                        if target_warehouse not in WAREHOUSE_IDS:
                            raise ValueError("Unknown warehouse")
                    except (ValueError, KeyError):
                        target_warehouse = None
                        target_day = args.date or today
                    request.unlink(missing_ok=True)
                last_start = now
                if target_warehouse is None:
                    automatic_started = now
                    next_run = now + args.every_minutes * 60
                else:
                    automatic_started = next_run - args.every_minutes * 60
                state = {
                    "started": now, "running": True, "automatic_started": automatic_started,
                    "warehouse": target_warehouse or "All warehouses", "date": str(target_day),
                }
                temporary = state_path.with_suffix(".tmp")
                temporary.write_text(json.dumps(state))
                temporary.replace(state_path)
                print(f"正在刷新 {state['warehouse']} · {target_day}…", flush=True)
                cleanup(Path(__file__).with_name("iwms-exports"), today)
                status = refresh(args.url, target_day, args.account_menu_selector, target_warehouse)
                cleanup(Path(__file__).with_name("iwms-exports"), today)
                state.update(running=False, finished=time(), success=status == 0)
                temporary.write_text(json.dumps(state))
                temporary.replace(state_path)
                if args.once:
                    return status
                print("等待下一次自动刷新或页面刷新请求。", flush=True)
            elif args.once:
                print("距离上轮启动不足设定间隔，请稍后再试。")
                return 1
            sleep(5)
    except KeyboardInterrupt:
        return 130
    finally:
        lock.close()


if __name__ == "__main__":
    raise SystemExit(main())
