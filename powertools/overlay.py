"""Animated pill at the bottom centre of the screen (tkinter, always on top, ignores the mouse focus).

  recording     pulsing red dot + bars that follow the microphone level
  transcribing  three amber dots in a wave
  error         grey pill with "!" for 2.5 s

tkinter runs on the main thread; other threads only put states into a queue that the Tk loop polls.
Without tkinter (embeddable Python) NullOverlay keeps the app running; the tray icon still shows the state.
"""

from __future__ import annotations

import math
import queue
import threading
import time
from collections import deque
from typing import Callable

W, H, BOTTOM = 132, 36, 72          # pill size and distance from the bottom edge (above the taskbar)
KEY = "#010203"                     # transparent colour
BG, RED, AMBER, GREY, BAR = "#1e1f24", "#ff3b47", "#ffb020", "#8a8a90", "#f2f2f5"
FPS_MS = 33
N_BARS = 9


class NullOverlay:
    available = False

    def __init__(self, level: Callable[[], float] | None = None):
        self._stop = threading.Event()

    def show(self, state: str) -> None:
        pass

    def ask(self, title: str, text: str, on_yes, on_no, timeout: int = 45) -> bool:
        return False  # not shown; the caller falls back to the window

    def quit(self) -> None:
        self._stop.set()

    def run(self) -> None:
        while not self._stop.wait(0.5):  # short waits so Ctrl+C gets through
            pass


class Overlay:
    available = True

    def __init__(self, level: Callable[[], float] | None = None):
        import tkinter as tk

        self.level = level or (lambda: 0.0)
        self._q: queue.Queue = queue.Queue()
        self.state = "idle"
        self.since = time.time()
        self.levels: deque = deque([0.0] * N_BARS, maxlen=N_BARS)

        self.root = tk.Tk()
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.configure(bg=KEY)
        try:
            self.root.attributes("-transparentcolor", KEY)
            self.root.attributes("-alpha", 0.96)
        except tk.TclError:
            pass
        self.c = tk.Canvas(self.root, width=W, height=H, bg=KEY, highlightthickness=0)
        self.c.pack()
        self.x = (self.root.winfo_screenwidth() - W) // 2
        self.y = self.root.winfo_screenheight() - H - BOTTOM
        # Shown once, now, and then only moved on/off screen: showing a window later could take the keyboard
        # focus away from the app the text must be pasted into.
        self._place(False)
        self.root.deiconify()
        self._no_focus()
        self.root.after(FPS_MS, self._tick)

    def _place(self, visible: bool) -> None:
        self.root.geometry(f"{W}x{H}+{self.x if visible else -3000}+{self.y if visible else -3000}")

    def _no_focus(self, widget=None, click_through: bool = True) -> None:
        """Windows: never activate, no taskbar button; the pill also lets clicks pass through."""
        try:
            import ctypes

            widget = widget or self.root
            widget.update_idletasks()
            hwnd = ctypes.windll.user32.GetParent(widget.winfo_id()) or widget.winfo_id()
            GWL_EXSTYLE, NOACTIVATE, TOOLWINDOW, TRANSPARENT, LAYERED = -20, 0x08000000, 0x80, 0x20, 0x80000
            style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE) | NOACTIVATE | TOOLWINDOW | LAYERED
            if click_through:
                style |= TRANSPARENT
            ctypes.windll.user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style)
        except Exception:
            pass  # not Windows

    def ask(self, title: str, text: str, on_yes, on_no, timeout: int = 45) -> bool:
        self._q.put(("ask", title, text, on_yes, on_no, timeout))
        return True

    def _ask(self, title: str, text: str, on_yes, on_no, timeout: int) -> None:
        """Card in the bottom-right corner with Record / Not now. It does not take the focus away from Teams."""
        import tkinter as tk

        win = tk.Toplevel(self.root)
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        win.configure(bg=BG, highlightthickness=1, highlightbackground="#3a3b42")
        answered = {"done": False}

        def answer(fn) -> None:
            if answered["done"]:
                return
            answered["done"] = True
            win.destroy()
            threading.Thread(target=fn, daemon=True).start()

        tk.Label(win, text=title, fg="#9aa0aa", bg=BG, font=("Segoe UI", 9)).pack(anchor="w", padx=14, pady=(10, 0))
        tk.Label(win, text=text, fg="white", bg=BG, font=("Segoe UI", 11, "bold"), wraplength=300,
                 justify="left").pack(anchor="w", padx=14, pady=(2, 10))
        row = tk.Frame(win, bg=BG)
        row.pack(anchor="e", padx=12, pady=(0, 12))
        for label, color, fn in (("Not now", "#3a3b42", on_no), ("Record", RED, on_yes)):
            b = tk.Label(row, text=label, fg="white", bg=color, padx=14, pady=5, cursor="hand2",
                         font=("Segoe UI", 10, "bold" if label == "Record" else "normal"))
            b.pack(side="left", padx=4)
            b.bind("<Button-1>", lambda _e, f=fn: answer(f))
        win.update_idletasks()
        w, h = max(330, win.winfo_reqwidth()), win.winfo_reqheight()
        win.geometry(f"{w}x{h}+{self.root.winfo_screenwidth() - w - 16}+{self.root.winfo_screenheight() - h - 64}")
        self._no_focus(win, click_through=False)
        win.after(timeout * 1000, lambda: answer(on_no))

    # ------------------------------------------------------------------ thread-safe API
    def show(self, state: str) -> None:
        self._q.put(state)

    def quit(self) -> None:
        self._q.put("__quit__")

    def run(self) -> None:
        self.root.mainloop()

    # ------------------------------------------------------------------ Tk thread
    def _pill(self, fill: str) -> None:
        r = H // 2
        self.c.create_oval(0, 0, H, H, fill=fill, outline=fill)
        self.c.create_oval(W - H, 0, W, H, fill=fill, outline=fill)
        self.c.create_rectangle(r, 0, W - r, H, fill=fill, outline=fill)

    def _draw(self) -> None:
        c, t = self.c, time.time() - self.since
        c.delete("all")
        if self.state == "recording":
            self._pill(BG)
            pulse = 5 + 1.8 * (1 + math.sin(t * 6)) / 2           # dot radius 5..6.8
            c.create_oval(20 - pulse - 4, H / 2 - pulse - 4, 20 + pulse + 4, H / 2 + pulse + 4,
                          fill="#3a1f24", outline="")                 # soft halo
            c.create_oval(20 - pulse, H / 2 - pulse, 20 + pulse, H / 2 + pulse, fill=RED, outline="")
            self.levels.append(min(1.0, self.level() * 9))
            x0, gap, bw = 40, 9, 4
            for i, lv in enumerate(self.levels):
                idle = 0.12 + 0.06 * math.sin(t * 5 + i)              # gentle motion even in silence
                h = 4 + (H - 14) * max(lv, idle)
                x = x0 + i * gap
                c.create_rectangle(x, H / 2 - h / 2, x + bw, H / 2 + h / 2, fill=BAR, outline="")
        elif self.state == "transcribing":
            self._pill(BG)
            for i in range(3):
                dy = 6 * math.sin(t * 7 - i * 0.9)
                cx = W / 2 - 18 + i * 18
                c.create_oval(cx - 5, H / 2 - 5 - dy, cx + 5, H / 2 + 5 - dy, fill=AMBER, outline="")
        elif self.state == "error":
            self._pill(GREY)
            c.create_text(W / 2, H / 2, text="!", fill="white", font=("Segoe UI", 14, "bold"))

    def _tick(self) -> None:
        try:
            while True:
                state = self._q.get_nowait()
                if isinstance(state, tuple) and state[0] == "ask":
                    self._ask(*state[1:])
                    continue
                if state == "__quit__":
                    self.root.quit()
                    return
                self.state, self.since = state, time.time()
                if state in ("recording", "transcribing", "error"):
                    self.levels.extend([0.0] * N_BARS)
                    self._place(True)
                    self.root.attributes("-topmost", True)
                else:
                    self._place(False)
        except queue.Empty:
            pass
        if self.state == "error" and time.time() - self.since > 2.5:
            self.state = "idle"
            self._place(False)
        if self.state in ("recording", "transcribing", "error"):
            self._draw()
        self.root.after(FPS_MS, self._tick)


def make_overlay(level: Callable[[], float] | None = None):
    """Windows: Win32 layered windows (no tkinter needed). Elsewhere: tkinter. Last resort: no overlay."""
    import sys

    if sys.platform == "win32":
        try:
            from .winoverlay import WinOverlay
            return WinOverlay(level)
        except Exception:
            pass
    try:
        return Overlay(level)
    except Exception:
        return NullOverlay(level)
