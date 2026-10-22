"""H: the app shell works in user space: tray icon with a menu, a notification (toast), a local web page
that talks back to Python, a global hotkey, and paste through the clipboard (restoring it afterwards)."""

from __future__ import annotations

import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .common import FAIL, IS_WIN, OK, SKIP, WARN, Context

PASTE_TEXT = "powertools paste test 123"
PAGE = """<!doctype html><meta charset="utf-8"><title>powertools check</title>
<body style="font:16px system-ui;max-width:640px;margin:40px auto">
<h2>powertools check</h2>
<p>1. Click the button: <button id="ok" style="font-size:16px">It works</button> <span id="done"></span></p>
<p>2. When the console asks: click into the box below and wait. The checker pastes a test text.</p>
<textarea id="t" rows="3" style="width:100%;font-size:16px"></textarea>
<script>
ok.onclick = () => fetch('/ok', {method: 'POST'}).then(() => done.textContent = 'thanks');
t.oninput = () => fetch('/paste', {method: 'POST', body: t.value});
</script>"""


def wait_for(flag: dict, key: str, seconds: int) -> bool:
    end = time.time() + seconds
    while time.time() < end:
        if flag.get(key):
            return True
        time.sleep(0.2)
    return False


def make_server(state: dict) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = PAGE.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            data = self.rfile.read(int(self.headers.get("Content-Length") or 0)).decode("utf-8", "replace")
            if self.path == "/ok":
                state["page_ok"] = True
            elif self.path == "/paste" and PASTE_TEXT in data:
                state["pasted"] = True
            self.send_response(204)
            self.end_headers()

        def log_message(self, *args):
            pass

    return ThreadingHTTPServer(("127.0.0.1", 0), Handler)


def steps(ctx: Context, icon, state: dict, url: str) -> None:
    try:
        icon.visible = True
        print("\n   a) Find the red dot in the tray (maybe under ^), right-click it, click 'Click me'. 30 s ...")
        ctx.add("ui.tray", OK if wait_for(state, "menu", 30) else WARN,
                "menu click received" if state.get("menu") else "no click within 30 s")

        try:
            icon.notify("If you can read this, notifications work.", "powertools check")
            seen = ctx.ask("   b) Did a notification appear? [y/n]: ").lower().startswith("y")
            ctx.add("ui.toast", OK if seen else WARN, "seen" if seen else "not seen (Focus Assist / policy?)")
        except Exception as e:
            ctx.add("ui.toast", FAIL, f"{type(e).__name__}: {e}"[:150])

        print(f"   c) A page opens in the browser ({url}). Click 'It works'. 30 s ...")
        webbrowser.open(url)
        ctx.add("ui.webpage", OK if wait_for(state, "page_ok", 30) else WARN,
                "page loaded and called back" if state.get("page_ok") else "no callback within 30 s")

        try:
            import keyboard

            keyboard.add_hotkey("alt+q", lambda: state.__setitem__("hotkey", True))
            print("   d) Press Alt+Q (anywhere). 15 s ...")
            ctx.add("ui.hotkey", OK if wait_for(state, "hotkey", 15) else WARN,
                    "global Alt+Q received" if state.get("hotkey") else "not received")
            keyboard.unhook_all_hotkeys()
        except Exception as e:
            ctx.add("ui.hotkey", FAIL, f"{type(e).__name__}: {e}"[:150])
            return

        try:
            import pyperclip

            before = pyperclip.paste()
            pyperclip.copy(PASTE_TEXT)
            print("   e) Click into the text box on the page now. Pasting in 5 s ...")
            time.sleep(5)
            keyboard.send("ctrl+v")
            pasted = wait_for(state, "pasted", 5)
            pyperclip.copy(before)
            restored = pyperclip.paste() == before
            ctx.add("ui.paste", OK if pasted else WARN, "Ctrl+V into the browser worked" if pasted else "nothing pasted")
            ctx.add("ui.clipboard_restore", OK if restored else WARN, "previous clipboard restored" if restored else "not restored")
        except Exception as e:
            ctx.add("ui.paste", FAIL, f"{type(e).__name__}: {e}"[:150])
    finally:
        icon.stop()


def run(ctx: Context) -> None:
    if not IS_WIN or not ctx.interactive:
        ctx.add("ui", SKIP, "needs Windows and a person at the keyboard")
        return
    if not ctx.confirm_start("Tray, toast, page, hotkey, paste", "About 2 minutes. Follow the prompts below."):
        ctx.add("ui", SKIP, "skipped by user")
        return
    try:
        import pystray
        from PIL import Image, ImageDraw
    except Exception as e:
        ctx.add("ui.import", FAIL, f"{type(e).__name__}: {e}")
        return

    state: dict = {}
    server = make_server(state)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_address[1]}/"

    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    ImageDraw.Draw(img).ellipse((8, 8, 56, 56), fill=(220, 30, 30, 255))
    icon = pystray.Icon("powertools-check", img, "powertools check",
                        menu=pystray.Menu(pystray.MenuItem("Click me", lambda: state.__setitem__("menu", True))))
    try:
        icon.run(setup=lambda i: steps(ctx, i, state, url))  # tray loop on the main thread, steps in another
    finally:
        server.shutdown()
