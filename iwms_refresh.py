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


def refresh(url, day, account_menu_selector):
    script = Path(__file__).with_name("iwms_fixed.py")
    command = [
        sys.executable, "-u", str(script), url, "--all-warehouses",
        "--profile-warehouse", "LAX1", "--date", day.isoformat(),
        "--click-login", "--strict",
    ]
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
            last_start = json.loads(state_path.read_text()).get("started", 0)
            next_run = last_start + args.every_minutes * 60
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
                request.unlink(missing_ok=True)
                last_start = now
                state = {"started": now, "running": True}
                state_path.write_text(json.dumps(state))
                today = datetime.now(TIMEZONE).date()
                cleanup(Path(__file__).with_name("iwms-exports"), today)
                status = refresh(args.url, args.date or today, args.account_menu_selector)
                cleanup(Path(__file__).with_name("iwms-exports"), today)
                state.update(running=False, finished=time(), success=status == 0)
                temporary = state_path.with_suffix(".tmp")
                temporary.write_text(json.dumps(state))
                temporary.replace(state_path)
                if args.once:
                    return status
                next_run = last_start + args.every_minutes * 60
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
