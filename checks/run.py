"""Run the feasibility checks and write checks/out/report-<time>.txt and .json.

    python -m checks.run                     all checks (Whisper only scans for models)
    python -m checks.run --only graph,audio
    python -m checks.run --only whisper --whisper --model turbo
    python -m checks.run --dry               non-interactive smoke run (any OS)
"""

from __future__ import annotations

import argparse
import importlib
import json
import platform
import time
import traceback
from dataclasses import asdict

from .common import FAIL, OUT_DIR, Context, section

ORDER = ["env", "whisper", "meeting", "graph", "audio", "ui"]
TITLES = {
    "env": "Environment (admin, Python, GPU, network)",
    "whisper": "Whisper models (cache, download, optional load test)",
    "meeting": "Meeting detection (microphone in use, Teams process, calendar)",
    "graph": "Microsoft Graph with a Graph Explorer token (Teams, mail, calendar, files, transcripts)",
    "audio": "Recording: microphone + system sound (loopback) at the same time",
    "ui": "Tray icon, toast, local web page, global hotkey, clipboard paste",
}


def main() -> int:
    ap = argparse.ArgumentParser(prog="python -m checks.run")
    ap.add_argument("--only", default="", help="comma-separated: " + ",".join(ORDER))
    ap.add_argument("--whisper", action="store_true", help="load the Whisper model and transcribe a sample")
    ap.add_argument("--model", default="turbo")
    ap.add_argument("--seconds", type=int, default=10, help="audio recording length")
    ap.add_argument("--dry", action="store_true", help="non-interactive; skips anything that needs a person")
    args = ap.parse_args()

    names = [n.strip() for n in args.only.split(",") if n.strip()] or ORDER
    unknown = [n for n in names if n not in ORDER]
    if unknown:
        ap.error(f"unknown check(s): {', '.join(unknown)}")

    ctx = Context(interactive=not args.dry, whisper=args.whisper, model=args.model, seconds=args.seconds)
    started = time.strftime("%Y-%m-%d %H:%M:%S")
    for name in names:
        section(TITLES[name])
        try:
            importlib.import_module(f"checks.{name}").run(ctx)
        except KeyboardInterrupt:
            ctx.add(name, FAIL, "interrupted")
        except Exception as e:  # a crashing check must not stop the others
            ctx.add(name, FAIL, f"crashed: {type(e).__name__}: {e}")
            traceback.print_exc()

    OUT_DIR.mkdir(exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    lines = [f"powertools check report  {started}  ({platform.platform()})", f"checks: {', '.join(names)}", ""]
    width = max((len(r.id) for r in ctx.results), default=10)
    lines += [f"[{r.status:<4}] {r.id:<{width}}  {r.detail}" for r in ctx.results]
    counts = {s: sum(r.status == s for r in ctx.results) for s in ("OK", "WARN", "FAIL", "SKIP")}
    lines += ["", "summary: " + ", ".join(f"{k} {v}" for k, v in counts.items())]
    txt = OUT_DIR / f"report-{stamp}.txt"
    txt.write_text("\n".join(lines) + "\n", encoding="utf-8")
    (OUT_DIR / f"report-{stamp}.json").write_text(
        json.dumps({"started": started, "checks": names, "results": [asdict(r) for r in ctx.results]}, indent=2),
        encoding="utf-8",
    )
    print("\n" + "\n".join(lines))
    print(f"\nReport saved: {txt}\nPaste the .txt into the chat. It holds no message content.")
    return 1 if counts["FAIL"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
