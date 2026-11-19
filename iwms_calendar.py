"""Local receipt windows and packing deadlines."""
import pandas as pd

TIMEZONE = "America/Los_Angeles"


def backlog_window(day):
    target = pd.Timestamp(day).normalize()
    # Weekend receipts belong to Monday's deadline cohort.
    if target.weekday() >= 5:
        target += pd.Timedelta(days=7 - target.weekday())
    days_back = 3 if target.weekday() == 0 else 1
    left = (target - pd.Timedelta(days=days_back) + pd.Timedelta(hours=15)).tz_localize(TIMEZONE)
    right = (target + pd.Timedelta(hours=15)).tz_localize(TIMEZONE)
    deadline = (target + pd.Timedelta(hours=17, minutes=30)).tz_localize(TIMEZONE)
    return left, right, deadline
