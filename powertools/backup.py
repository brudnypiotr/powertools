"""A copy of the app's state and the memory before an update. No tokens, no recordings."""

from __future__ import annotations

import time
import zipfile
from pathlib import Path

KEEP = 5
STATE = ("assistant.json", "settings.json", "usage.json")


def make(home: Path, memory: Path, version: str) -> Path:
    folder = home / "backups"
    folder.mkdir(parents=True, exist_ok=True)
    out = folder / f"{time.strftime('%Y%m%d-%H%M%S')}-{time.time_ns() // 1000 % 1000000:06d}-before-update-of-{version}.zip"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for name in STATE:
            if (home / name).is_file():
                z.write(home / name, name)
        for p in sorted(memory.rglob("*.md")):
            rel = p.relative_to(memory)
            if not any(x.startswith(".") for x in rel.parts):
                z.write(p, f"memory/{rel.as_posix()}")
    for old in sorted(folder.glob("*.zip"))[:-KEEP]:
        old.unlink(missing_ok=True)
    return out
