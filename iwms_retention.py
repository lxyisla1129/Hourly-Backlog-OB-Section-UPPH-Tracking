"""Remove expired managed exports, retaining today and the preceding six dates."""
from datetime import timedelta
import re

SNAPSHOT = re.compile(r"LAX[1245]_(?:picking|packing|backlog)_(\d{4}-\d{2}-\d{2})\.(?:xlsx|xls)$")


def cleanup(directory, today):
    from datetime import date
    cutoff = today - timedelta(days=6)
    for path in directory.glob("*"):
        match = SNAPSHOT.fullmatch(path.name)
        if match and date.fromisoformat(match[1]) < cutoff:
            path.unlink(missing_ok=True)
