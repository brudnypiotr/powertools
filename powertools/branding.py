"""The look of an organisation's copy: name, colours, logo, header background. Never in the public repository.

Two folders: `branding/` in the copy (git-ignored; an organisation's own repository adds it by force) and, on top of it,
%LOCALAPPDATA%\\powertools\\branding (imported in Settings, so Update never meets a changed tracked file). A branding.zip
made for another app works too: logo and background are taken, its own app name is not (only "powertools_name" is read).
The red dot stays the icon.
"""

from __future__ import annotations

import io
import json
import os
import re
import zipfile
from pathlib import Path

from . import updater
from .config import data_dir

STEMS = ("logo", "background")
TYPES = {".svg": "image/svg+xml", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}
COLORS = ("accent", "sidebar", "sidebar_active")
HEX = re.compile(r"#[0-9a-f]{6}")
MAX_ZIP = 10_000_000
MAX_JSON = 100_000


def local_dir() -> Path:
    return data_dir() / "branding"


def dirs() -> list[Path]:
    """Where branding is looked for, the winner first."""
    return [local_dir(), updater.ROOT / "branding"]


def find(stem: str) -> Path | None:
    if stem not in STEMS:
        return None
    return next((p for d in dirs() for ext in TYPES if (p := d / f"{stem}{ext}").is_file()), None)


def clean(data) -> dict:
    """Name and colours that may be used; everything else in the file is ignored."""
    data = data if isinstance(data, dict) else {}
    colors = data.get("colors") if isinstance(data.get("colors"), dict) else {}
    out = {"powertools_name": " ".join(str(data.get("powertools_name") or "").split())[:40],
           "colors": {k: str(colors[k]).strip().lower() for k in COLORS if HEX.fullmatch(str(colors.get(k, "")).strip().lower())}}
    return {k: v for k, v in out.items() if v}


def _read(d: Path) -> dict:
    try:
        return clean(json.loads((d / "branding.json").read_text(encoding="utf-8-sig")))
    except (OSError, ValueError):
        return {}


def settings() -> dict:
    name, colors = "", {}
    for d in reversed(dirs()):
        s = _read(d)
        name, colors = s.get("powertools_name") or name, {**colors, **s.get("colors", {})}
    return {"name": name or "powertools", "colors": colors}


def state() -> dict:
    files = {s: find(s) for s in STEMS}
    stamp = {s: int(p.stat().st_mtime) for s, p in files.items() if p}
    return {**settings(), "files": stamp, "local": local_dir().is_dir() and any(local_dir().iterdir())}


def save(data: dict) -> dict:
    """Name and colours from Settings, into the local folder ('' removes one)."""
    for k, v in (data.get("colors") or {}).items():
        if k not in COLORS or (v and not HEX.fullmatch(str(v).strip().lower())):
            raise ValueError(f"{k}: a colour looks like #3366cc")
    old = _read(local_dir())
    _write_json({"powertools_name": data.get("powertools_name", old.get("powertools_name", "")),
                 "colors": {**old.get("colors", {}), **(data.get("colors") or {})}})
    return state()


def _write_json(data: dict) -> None:
    local_dir().mkdir(parents=True, exist_ok=True)
    tmp = local_dir() / "branding.tmp"
    tmp.write_text(json.dumps(clean(data), ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, local_dir() / "branding.json")


def import_zip(data: bytes) -> dict:
    """Logo, background and branding.json from a zip (flat or in a folder); nothing is written when the zip is bad."""
    if len(data) > MAX_ZIP:
        raise ValueError(f"the zip is larger than {MAX_ZIP // 1_000_000} MB")
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
        files, meta = {}, None
        for info in z.infolist():
            name = Path(info.filename).name.lower()      # the base name only: no path from the zip is trusted
            stem, ext = os.path.splitext(name)
            if name == "branding.json" and info.file_size <= MAX_JSON:
                meta = json.loads(z.read(info).decode("utf-8-sig"))
            elif not info.is_dir() and stem in STEMS and ext in TYPES and info.file_size <= MAX_ZIP:
                files[stem] = (name, z.read(info))
    except (zipfile.BadZipFile, UnicodeDecodeError, ValueError, RuntimeError, NotImplementedError) as e:
        raise ValueError(f"not a usable branding.zip: {e}") from e
    if not files and not clean(meta):
        raise ValueError("nothing to use in the zip (logo or background as .png, .svg or .jpg, branding.json)")
    local_dir().mkdir(parents=True, exist_ok=True)
    for stem, (name, body) in files.items():
        for ext in TYPES:
            (local_dir() / f"{stem}{ext}").unlink(missing_ok=True)   # one file per role
        (local_dir() / name).write_bytes(body)
    if clean(meta):
        old, new = _read(local_dir()), clean(meta)
        _write_json({**old, **new, "colors": {**old.get("colors", {}), **new.get("colors", {})}})
    return state()


def pack(folder: Path) -> Path:
    """What this copy shows, as branding.zip: unpacked in an organisation's repository it is everyone's look."""
    s = settings()
    meta = clean({"powertools_name": "" if s["name"] == "powertools" else s["name"], "colors": s["colors"]})
    files = [p for stem in STEMS if (p := find(stem))]
    if not files and not meta:
        raise ValueError("there is no branding to pack yet")
    out = folder / "branding.zip"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for p in files:
            z.write(p, f"branding/{p.name}")
        z.writestr("branding/branding.json", json.dumps(meta, ensure_ascii=False, indent=2))
    return out


def reset() -> dict:
    """Back to the organisation's look (or the neutral one): only the local folder's own files go."""
    for name in ["branding.json", *(f"{s}{e}" for s in STEMS for e in TYPES)]:
        (local_dir() / name).unlink(missing_ok=True)
    return state()


# ------------------------------------------------------------------ CSS
def _rgb(h: str) -> tuple[int, int, int]:
    return int(h[1:3], 16), int(h[3:5], 16), int(h[5:7], 16)


def mix(a: str, b: str, share: float) -> str:
    """`share` of b in a."""
    return "#" + "".join(f"{round(x + (y - x) * share):02x}" for x, y in zip(_rgb(a), _rgb(b)))


def contrast(a: str, b: str) -> float:
    def lum(h: str) -> float:
        r, g, b_ = (c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in (x / 255 for x in _rgb(h)))
        return 0.2126 * r + 0.7152 * g + 0.0722 * b_
    hi, lo = sorted((lum(a), lum(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def readable(color: str, on: str, towards: str, least: float = 4.5) -> str:
    """The colour, moved towards `towards` until text in it can be read on `on`."""
    for n in range(21):
        c = mix(color, towards, n / 20)
        if contrast(c, on) >= least:
            return c
    return towards


def css() -> str:
    """Overrides of the window's colours. Text stays readable whatever was picked."""
    c = settings()["colors"]
    light, dark = [], []
    if "accent" in c:
        a = c["accent"]
        for out, panel, towards in ((light, "#ffffff", "#000000"), (dark, "#1c1c21", "#ffffff")):
            shown = readable(a, panel, towards)
            soft = mix(panel, a, 0.12 if out is light else 0.25)
            out += [f"--accent: {shown}", f"--accent-soft: {soft}", f"--accent-text: {readable(shown, soft, towards)}",
                    f"--on-accent: {'#ffffff' if contrast('#ffffff', shown) >= 4.5 else '#14141a'}"]
    if "sidebar" in c:
        s = c["sidebar"]
        strong = "#ffffff" if contrast("#ffffff", s) >= contrast("#14141a", s) else "#14141a"
        active = c.get("sidebar_active") or mix(s, strong, 0.12)
        light += [f"--side: {s}", f"--side2: {active}", f"--side-strong: {strong}",
                  f"--side-active-text: {'#ffffff' if contrast('#ffffff', active) >= contrast('#14141a', active) else '#14141a'}",
                  f"--side-ink: {readable(mix(s, strong, 0.75), s, strong)}", f"--side-muted: {readable(mix(s, strong, 0.55), s, strong)}",
                  f"--side-hover: {mix(s, strong, 0.08)}", f"--side-line: {mix(s, strong, 0.2)}",
                  f"--side-accent: {readable(c.get('accent', strong), active, strong, 3)}"]
        dark += light[-9:]
    elif "sidebar_active" in c:
        light.append(f"--side2: {c['sidebar_active']}")
        dark.append(light[-1])
    out = f":root {{ {'; '.join(light)}; }}\n" if light else ""
    out += f"@media (prefers-color-scheme: dark) {{ :root {{ {'; '.join(dark)}; }} }}\n" if dark else ""
    if p := find("background"):
        out += f'.brand {{ background: url("/branding/background?{int(p.stat().st_mtime)}") center/cover; }}\n'
    return out
