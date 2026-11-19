"""Launch the local collector and dashboard together."""
import argparse
from pathlib import Path
import subprocess
import sys
import webbrowser

URL = "https://pulsar-login.jd.com/login.html?ReturnUrl=https%3A%2F%2Fiwms-us.jd.com%2F#/"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--login", action="store_true", help="Initialize SSO before starting services.")
    parser.add_argument("--share-lan", action="store_true", help="Listen on the company LAN, subject to firewall rules.")
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    if args.login:
        result = subprocess.run([
            sys.executable, str(root / "iwms_fixed.py"), URL,
            "--warehouse", "LAX1", "--profile-warehouse", "LAX1", "--login-only",
        ], cwd=root)
        if result.returncode:
            return result.returncode
    worker = subprocess.Popen([sys.executable, "-u", str(root / "iwms_refresh.py"), URL], cwd=root)
    dashboard = None
    try:
        command = [
            sys.executable, "-m", "streamlit", "run", str(root / "app_excel.py"),
            "--server.address", "0.0.0.0" if args.share_lan else "127.0.0.1",
            "--server.port", "8501", "--server.headless", "true",
        ]
        dashboard = subprocess.Popen(command, cwd=root)
        print("Dashboard URL: http://localhost:8501", flush=True)
        webbrowser.open("http://localhost:8501")
        return dashboard.wait()
    except KeyboardInterrupt:
        return 130
    finally:
        for process in (dashboard, worker):
            if process is not None and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()


if __name__ == "__main__":
    raise SystemExit(main())
