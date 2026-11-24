"""Reading and writing files that other programs use too (an editor, OneDrive): a file that is locked for a moment is never
taken for an empty one, writes are atomic, and a damaged state file is kept aside instead of being overwritten."""

from __future__ import annotations

import json
import locale
import logging
import os
import time
from pathlib import Path

log = logging.getLogger("powertools.files")
TRIES, WAIT = 5, 0.2


class Busy(OSError):
    """Another program holds the file right now; nothing was read or changed."""


def _patiently(path: Path, what):
    for n in range(TRIES):
        try:
            return what()
        except FileNotFoundError:
            raise
        except OSError as e:            # locked, or only in the cloud and OneDrive does not answer
            if n == TRIES - 1:
                raise Busy(f"{path.name} cannot be read or written now (an editor or OneDrive holds it): try again in a moment") from e
            time.sleep(WAIT)


def decode(raw: bytes) -> str:
    """UTF-8 (with or without BOM), else the code page of this Windows (Notepad's ANSI)."""
    for enc in ("utf-8-sig", locale.getpreferredencoding(False), "cp1250"):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace")


def read_text(path: Path) -> str:
    """The text with \\n line ends, "" when there is no such file; Busy when it cannot be read now."""
    try:
        return decode(_patiently(path, path.read_bytes)).replace("\r\n", "\n")
    except FileNotFoundError:
        return ""


def write_text(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    _patiently(path, lambda: os.replace(tmp, path))
    return path


def load_json(path: Path) -> dict:
    """{} when there is no file. A damaged one is renamed to <name>.bad-<time>, so nothing is lost for good."""
    text = read_text(path)
    if not text:
        return {}
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except ValueError:
        pass
    bad = path.with_name(f"{path.stem}.bad-{time.strftime('%Y%m%d-%H%M%S')}{path.suffix}")
    _patiently(path, lambda: os.replace(path, bad))
    log.error("%s was damaged: kept as %s", path.name, bad.name)
    return {}


def save_json(path: Path, data, **kw) -> None:
    write_text(path, json.dumps(data, ensure_ascii=False, **kw))
