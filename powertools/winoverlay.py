"""The overlay on Windows without tkinter: layered Win32 windows (per-pixel alpha) fed with images from
overlay_art. Works on any Python, including the embeddable one.

- The pill never activates and lets clicks through (WS_EX_NOACTIVATE | WS_EX_TRANSPARENT), so Ctrl+V still goes
  to the app the user is typing in.
- The "Record?" card takes clicks but never the focus (WM_MOUSEACTIVATE -> MA_NOACTIVATE).
- All windows live on the thread that calls run() (the main thread); other threads only queue commands.
"""

from __future__ import annotations

import ctypes
import logging
import queue
import threading
import time
from collections import deque
from ctypes import wintypes as wt

import numpy as np

from . import overlay_art as art

log = logging.getLogger("powertools.overlay")

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)

WS_POPUP = 0x80000000
WS_EX_LAYERED, WS_EX_TRANSPARENT, WS_EX_TOPMOST = 0x80000, 0x20, 0x8
WS_EX_TOOLWINDOW, WS_EX_NOACTIVATE = 0x80, 0x08000000
WM_DESTROY, WM_TIMER, WM_MOUSEMOVE, WM_LBUTTONUP, WM_MOUSEACTIVATE, WM_SETCURSOR = 0x2, 0x113, 0x200, 0x202, 0x21, 0x20
WM_APP = 0x8000
SW_HIDE, SW_SHOWNOACTIVATE = 0, 4
MA_NOACTIVATE = 3
ULW_ALPHA = 2
HWND_TOPMOST = wt.HWND(-1)
SWP_NOSIZE, SWP_NOMOVE, SWP_NOACTIVATE = 0x1, 0x2, 0x10
SPI_GETWORKAREA = 0x30
IDC_ARROW, IDC_HAND = 32512, 32649
FRAME_MS = 33


class WNDCLASSEXW(ctypes.Structure):
    _fields_ = [("cbSize", wt.UINT), ("style", wt.UINT), ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int), ("hInstance", wt.HINSTANCE), ("hIcon", wt.HICON),
                ("hCursor", wt.HANDLE), ("hbrBackground", wt.HBRUSH), ("lpszMenuName", wt.LPCWSTR),
                ("lpszClassName", wt.LPCWSTR), ("hIconSm", wt.HICON)]


class BLENDFUNCTION(ctypes.Structure):
    _fields_ = [("BlendOp", ctypes.c_byte), ("BlendFlags", ctypes.c_byte),
                ("SourceConstantAlpha", ctypes.c_ubyte), ("AlphaFormat", ctypes.c_byte)]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG), ("biPlanes", wt.WORD),
                ("biBitCount", wt.WORD), ("biCompression", wt.DWORD), ("biSizeImage", wt.DWORD),
                ("biXPelsPerMeter", wt.LONG), ("biYPelsPerMeter", wt.LONG), ("biClrUsed", wt.DWORD),
                ("biClrImportant", wt.DWORD)]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wt.DWORD * 3)]


def _sig(fn, args, res=None):
    fn.argtypes = args
    if res is not None:
        fn.restype = res


_sig(user32.DefWindowProcW, [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM], LRESULT)
_sig(user32.RegisterClassExW, [ctypes.POINTER(WNDCLASSEXW)], wt.ATOM)
_sig(user32.CreateWindowExW, [wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                              ctypes.c_int, wt.HWND, wt.HMENU, wt.HINSTANCE, wt.LPVOID], wt.HWND)
_sig(user32.ShowWindow, [wt.HWND, ctypes.c_int], wt.BOOL)
_sig(user32.DestroyWindow, [wt.HWND], wt.BOOL)
_sig(user32.SetTimer, [wt.HWND, ctypes.c_size_t, wt.UINT, ctypes.c_void_p], ctypes.c_size_t)
_sig(user32.GetMessageW, [ctypes.POINTER(wt.MSG), wt.HWND, wt.UINT, wt.UINT], wt.BOOL)
_sig(user32.TranslateMessage, [ctypes.POINTER(wt.MSG)], wt.BOOL)
_sig(user32.DispatchMessageW, [ctypes.POINTER(wt.MSG)], LRESULT)
_sig(user32.PostMessageW, [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM], wt.BOOL)
_sig(user32.PostQuitMessage, [ctypes.c_int])
_sig(user32.GetDC, [wt.HWND], wt.HDC)
_sig(user32.ReleaseDC, [wt.HWND, wt.HDC], ctypes.c_int)
_sig(user32.UpdateLayeredWindow, [wt.HWND, wt.HDC, ctypes.POINTER(wt.POINT), ctypes.POINTER(wt.SIZE), wt.HDC,
                                  ctypes.POINTER(wt.POINT), wt.COLORREF, ctypes.POINTER(BLENDFUNCTION), wt.DWORD], wt.BOOL)
_sig(user32.SetWindowPos, [wt.HWND, wt.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wt.UINT], wt.BOOL)
_sig(user32.SystemParametersInfoW, [wt.UINT, wt.UINT, ctypes.c_void_p, wt.UINT], wt.BOOL)
_sig(user32.LoadCursorW, [wt.HINSTANCE, ctypes.c_void_p], wt.HANDLE)
_sig(user32.SetCursor, [wt.HANDLE], wt.HANDLE)
_sig(gdi32.CreateCompatibleDC, [wt.HDC], wt.HDC)
_sig(gdi32.CreateDIBSection, [wt.HDC, ctypes.POINTER(BITMAPINFO), wt.UINT, ctypes.POINTER(ctypes.c_void_p),
                              wt.HANDLE, wt.DWORD], wt.HBITMAP)
_sig(gdi32.SelectObject, [wt.HDC, wt.HGDIOBJ], wt.HGDIOBJ)
_sig(gdi32.DeleteObject, [wt.HGDIOBJ], wt.BOOL)
_sig(gdi32.DeleteDC, [wt.HDC], wt.BOOL)
_sig(kernel32.GetModuleHandleW, [wt.LPCWSTR], wt.HMODULE)


def make_dpi_aware() -> None:
    """Call before any window exists, so sizes are in real pixels on scaled displays."""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            user32.SetProcessDPIAware()
        except Exception:
            pass


def dpi_scale() -> float:
    try:
        return user32.GetDpiForSystem() / 96.0
    except Exception:
        return 1.0


def work_area() -> tuple[int, int, int, int]:
    r = wt.RECT()
    user32.SystemParametersInfoW(SPI_GETWORKAREA, 0, ctypes.byref(r), 0)
    return r.left, r.top, r.right, r.bottom


def blit(hwnd, img, x: int, y: int) -> bool:
    """Put an RGBA PIL image on a layered window at (x, y) with per-pixel alpha."""
    w, h = img.size
    arr = np.asarray(img, dtype=np.uint16)
    a = arr[..., 3:4]
    bgra = np.concatenate([(arr[..., 2::-1] * a // 255), a], axis=2).astype(np.uint8)  # premultiplied BGRA
    screen = user32.GetDC(None)
    mem = gdi32.CreateCompatibleDC(screen)
    bmi = BITMAPINFO()
    bmi.bmiHeader = BITMAPINFOHEADER(ctypes.sizeof(BITMAPINFOHEADER), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
    bits = ctypes.c_void_p()
    hbmp = gdi32.CreateDIBSection(mem, ctypes.byref(bmi), 0, ctypes.byref(bits), None, 0)
    ok = False
    try:
        ctypes.memmove(bits, bgra.tobytes(), w * h * 4)
        old = gdi32.SelectObject(mem, hbmp)
        blend = BLENDFUNCTION(0, 0, 255, 1)  # AC_SRC_OVER, AC_SRC_ALPHA
        ok = bool(user32.UpdateLayeredWindow(hwnd, screen, ctypes.byref(wt.POINT(x, y)), ctypes.byref(wt.SIZE(w, h)),
                                             mem, ctypes.byref(wt.POINT(0, 0)), 0, ctypes.byref(blend), ULW_ALPHA))
        gdi32.SelectObject(mem, old)
    finally:
        gdi32.DeleteObject(hbmp)
        gdi32.DeleteDC(mem)
        user32.ReleaseDC(None, screen)
    return ok


class WinOverlay:
    available = True

    def __init__(self, level=None):
        self.level = level or (lambda: 0.0)
        self._q: queue.Queue = queue.Queue()
        self.state, self.since = "idle", time.time()
        self.levels: deque = deque([0.0] * 12, maxlen=12)
        self.pill_hwnd = None
        self.card_hwnd = None
        self.card: dict = {}
        self.frames = 0
        self.failed_blits = 0
        self._ready = threading.Event()
        self._proc = WNDPROC(self._wndproc)  # keep a reference: Windows calls it
        self.scale = dpi_scale()

    # ------------------------------------------------------------------ any thread
    def show(self, state: str) -> None:
        self._post(("state", state))

    def ask(self, title: str, text: str, on_yes, on_no, timeout: int = 45) -> bool:
        self._post(("ask", title, text, on_yes, on_no, timeout))
        return True

    def quit(self) -> None:
        self._post(("quit",))

    def _post(self, item) -> None:
        self._q.put(item)
        if self.pill_hwnd:
            user32.PostMessageW(self.pill_hwnd, WM_APP, 0, 0)

    # ------------------------------------------------------------------ overlay thread
    def _create(self) -> None:
        hinst = kernel32.GetModuleHandleW(None)
        wc = WNDCLASSEXW()
        wc.cbSize = ctypes.sizeof(WNDCLASSEXW)
        wc.lpfnWndProc = self._proc
        wc.hInstance = hinst
        wc.hCursor = user32.LoadCursorW(None, ctypes.c_void_p(IDC_ARROW))
        wc.lpszClassName = "powertools_overlay"
        if not user32.RegisterClassExW(ctypes.byref(wc)) and ctypes.get_last_error() != 1410:  # 1410: exists
            raise OSError(f"RegisterClassExW failed ({ctypes.get_last_error()})")
        base = WS_EX_LAYERED | WS_EX_TOPMOST | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE
        self.pill_hwnd = user32.CreateWindowExW(base | WS_EX_TRANSPARENT, wc.lpszClassName, "powertools", WS_POPUP,
                                                0, 0, 1, 1, None, None, hinst, None)
        self.card_hwnd = user32.CreateWindowExW(base, wc.lpszClassName, "powertools ask", WS_POPUP,
                                                0, 0, 1, 1, None, None, hinst, None)
        if not self.pill_hwnd or not self.card_hwnd:
            raise OSError(f"CreateWindowExW failed ({ctypes.get_last_error()})")
        user32.SetTimer(self.pill_hwnd, 1, FRAME_MS, None)

    def _wndproc(self, hwnd, msg, wparam, lparam):
        try:
            if msg in (WM_TIMER, WM_APP) and hwnd == self.pill_hwnd:
                self._tick()
                return 0
            if hwnd == self.card_hwnd and self.card:
                if msg == WM_MOUSEACTIVATE:
                    return MA_NOACTIVATE
                if msg in (WM_MOUSEMOVE, WM_LBUTTONUP, WM_SETCURSOR):
                    if msg == WM_SETCURSOR:
                        over = self.card.get("hover")
                        user32.SetCursor(user32.LoadCursorW(None, ctypes.c_void_p(IDC_HAND if over else IDC_ARROW)))
                        return 1
                    x = ctypes.c_short(lparam & 0xFFFF).value
                    y = ctypes.c_short((lparam >> 16) & 0xFFFF).value
                    hit = next((n for n, (x0, y0, x1, y1) in self.card["boxes"].items() if x0 <= x <= x1 and y0 <= y <= y1), "")
                    if msg == WM_MOUSEMOVE and hit != self.card.get("hover"):
                        self.card["hover"] = hit
                        self._draw_card()
                    elif msg == WM_LBUTTONUP and hit:
                        self._answer("on_yes" if hit == "yes" else "on_no")
                    return 0
            if msg == WM_DESTROY and hwnd == self.pill_hwnd:
                user32.PostQuitMessage(0)
                return 0
        except Exception:
            log.exception("overlay window procedure failed")
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _tick(self) -> None:
        while True:
            try:
                item = self._q.get_nowait()
            except queue.Empty:
                break
            kind = item[0]
            if kind == "quit":
                self._hide_card()
                user32.DestroyWindow(self.card_hwnd)
                user32.DestroyWindow(self.pill_hwnd)
                return
            if kind == "state":
                self.state, self.since = item[1], time.time()
                if self.state in ("recording", "transcribing", "error"):
                    self.levels.extend([0.0] * 12)
                    self._draw_pill()
                    user32.ShowWindow(self.pill_hwnd, SW_SHOWNOACTIVATE)
                    user32.SetWindowPos(self.pill_hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)
                else:
                    user32.ShowWindow(self.pill_hwnd, SW_HIDE)
            elif kind == "ask":
                _, title, text, on_yes, on_no, timeout = item
                self.card = {"title": title, "text": text, "on_yes": on_yes, "on_no": on_no, "hover": "",
                             "deadline": time.time() + timeout, "boxes": {}}
                self._draw_card()
                user32.ShowWindow(self.card_hwnd, SW_SHOWNOACTIVATE)
                user32.SetWindowPos(self.card_hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)
        if self.state == "error" and time.time() - self.since > 2.5:
            self.state = "idle"
            user32.ShowWindow(self.pill_hwnd, SW_HIDE)
        if self.state in ("recording", "transcribing", "error"):
            if self.state == "recording":
                self.levels.append(min(1.0, self.level() * 9))
            self._draw_pill()
        if self.card and time.time() > self.card["deadline"]:
            self._answer("on_no")

    def _draw_pill(self) -> None:
        img = art.pill(self.state, time.time() - self.since, list(self.levels), self.scale)
        left, top, right, bottom = work_area()
        x = left + (right - left - img.size[0]) // 2
        y = bottom - img.size[1] - int(24 * self.scale)
        if blit(self.pill_hwnd, img, x, y):
            self.frames += 1
        else:
            self.failed_blits += 1

    def _draw_card(self) -> None:
        img, boxes = art.card(self.card["title"], self.card["text"], self.card.get("hover", ""), self.scale)
        self.card["boxes"] = boxes
        left, top, right, bottom = work_area()
        blit(self.card_hwnd, img, right - img.size[0] - int(16 * self.scale), bottom - img.size[1] - int(16 * self.scale))

    def _hide_card(self) -> None:
        if self.card_hwnd:
            user32.ShowWindow(self.card_hwnd, SW_HIDE)

    def _answer(self, key: str) -> None:
        card, self.card = self.card, {}
        self._hide_card()
        if card.get(key):
            threading.Thread(target=card[key], daemon=True).start()

    def run(self) -> None:
        """Blocks (main thread) until quit()."""
        self._create()
        self._ready.set()
        user32.PostMessageW(self.pill_hwnd, WM_APP, 0, 0)  # commands queued before the window existed
        msg = wt.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
