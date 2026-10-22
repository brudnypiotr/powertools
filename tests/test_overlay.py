"""Overlay drawing everywhere; the real Win32 layered windows on Windows (CI runner has a desktop)."""

import sys
import threading
import time

import pytest

from powertools import overlay_art as art


def test_pill_states_render():
    for state in ("recording", "transcribing", "error"):
        img = art.pill(state, 0.4, [0.5] * 12, scale=1.25)
        assert img.size == (int(art.PILL_W * 1.25), int(art.PILL_H * 1.25))
        assert img.getchannel("A").getextrema()[1] > 200  # something opaque was drawn


def test_card_buttons_inside_image():
    img, boxes = art.card("Teams call", "Record 'A very long meeting title for the weekly sync'? (12 people)")
    w, h = img.size
    assert set(boxes) == {"yes", "no"}
    for x0, y0, x1, y1 in boxes.values():
        assert 0 <= x0 < x1 <= w and 0 <= y0 < y1 <= h
    assert boxes["no"][2] <= boxes["yes"][0]  # "Not now" left of "Record"


@pytest.mark.skipif(sys.platform != "win32", reason="Win32 only")
def test_win_overlay_shows_animates_asks_and_quits():
    from powertools.winoverlay import WinOverlay

    ov = WinOverlay(level=lambda: 0.05)
    answers = []

    def drive():
        ov._ready.wait(5)
        ov.show("recording")
        time.sleep(0.6)
        ov.show("transcribing")
        time.sleep(0.3)
        ov.ask("Teams call", "Record 'Test'?", lambda: answers.append("yes"), lambda: answers.append("no"), timeout=1)
        time.sleep(1.5)
        ov.show("idle")
        ov.quit()

    t = threading.Thread(target=drive)
    t.start()
    ov.run()  # returns after quit()
    t.join(5)
    assert ov.frames > 5, (ov.frames, ov.failed_blits)
    assert ov.failed_blits == 0
    time.sleep(0.2)
    assert answers == ["no"]  # the card timed out
