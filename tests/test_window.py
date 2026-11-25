"""The window in a real browser against the demo app. Skipped where Playwright or a browser is missing (CI has both)."""

import os
import re
import time

import pytest

sync = pytest.importorskip("playwright.sync_api")

from . import demo  # noqa: E402


@pytest.fixture(scope="module")
def browser():
    with sync.sync_playwright() as p:
        for kw in ({}, {"channel": "chrome"}, {"channel": "msedge"}):
            try:
                b = p.chromium.launch(**kw)
                break
            except sync.Error:
                continue
        else:
            (pytest.fail if os.environ.get("NEED_BROWSER") else pytest.skip)("no browser")
        yield b
        b.close()


@pytest.fixture()
def win(browser, tmp_path, monkeypatch):
    monkeypatch.setenv("POWERTOOLS_HOME", str(tmp_path))
    from powertools.web.server import serve

    class Win:
        def open(self, cut=0, setup=True):
            self.app, self.g = demo.build(tmp_path, cut)
            demo.token(self.app)
            if setup:
                self.app.settings.update({"ai_provider": "local", "setup_done": True})
            self.httpd = serve(self.app)
            self.page = browser.new_page(viewport={"width": 1180, "height": 740})
            self.page.set_default_timeout(8000)
            self.errors = []
            self.page.on("pageerror", lambda e: self.errors.append(str(e)))
            self.page.on("console", lambda m: self.errors.append(m.text) if m.type == "error" and "40" not in m.text else None)
            self.page.goto(f"http://127.0.0.1:{self.httpd.server_address[1]}/?t={self.app.door.key}")
            return self.page

        def update(self):
            self.page.select_option("#recap-range", "30")
            self.page.click("#recap-update")
            sync.expect(self.page.locator("#today-status")).to_contain_text("As of", timeout=15000)
            return self.page

        def row(self, text):
            self.page.locator("#today-rows .prow", has_text=text).first.click()

    w = Win()
    yield w
    if hasattr(w, "page"):
        w.page.close()
        w.httpd.shutdown()
        w.app.store.close()
        assert w.errors == []


def see(page, selector, text):
    sync.expect(page.locator(selector).first).to_contain_text(text)


def test_setup_then_update_then_brief(win):
    page = win.open(setup=False)
    page.check("#setup-form [value=local]")
    page.click("#setup-next")
    page.fill("[name=running]", "Alpha launch")
    page.click("#setup-next")
    page.click("#setup-next")
    see(page, "#today-body", "Press Start my day")
    win.update()
    see(page, "#today-rows", "Choose option A or B")
    page.click("[data-kind=day]")
    see(page, "#today-body", "Finance needs your choice")


def test_done_opens_the_next_item_and_can_be_undone_with_the_draft(win):
    page = win.open()
    win.update()
    win.row("Confirm the migration window")
    page.fill("#draft-text", "My own words")
    page.click("#today-bar [data-do=done]")
    sync.expect(page.locator("#today-rows")).not_to_contain_text("Confirm the migration window")
    page.click(".toast button")
    sync.expect(page.locator("#draft-text")).to_have_value("My own words")


def test_an_update_without_a_token_waits_and_goes_on_by_itself(win):
    page = win.open(cut=6)
    page.select_option("#recap-range", "30")
    page.click("#recap-update")
    see(page, "#today-body", "One step")
    see(page, "#side-graph", "expired")
    page.click("#token-paste summary")
    page.fill("#token-form [name=token]", demo.token(win.app))
    win.app.graph.tokens.clear()
    page.click("#token-form button")
    sync.expect(page.locator("#today-status")).to_contain_text("As of", timeout=15000)


def test_a_draft_goes_to_outlook_once(win):
    page = win.open()
    win.update()
    win.row("Confirm the migration window")
    page.click("#today-bar [data-do=outlook]")
    see(page, "#today-bar", "Open it in Outlook")
    page.fill("#draft-text", "other words")
    page.locator("#draft-text").blur()
    page.locator("#today-rows .prow").first.click()
    win.row("Confirm the migration window")
    see(page, "#today-bar", "New text to Outlook drafts")


def test_a_failed_update_says_why(win):
    page = win.open()
    win.update()
    win.app.assistant.updates.job.state, win.app.assistant.updates.job.error = "error", "no connection to Microsoft"
    page.reload()
    see(page, "#today-rows", "no connection to Microsoft")


def test_ask_answers_and_remembers(win):
    page = win.open()
    win.update()
    page.click("#chat-open")
    page.fill("#chat-q", "Who am I waiting for?")
    page.keyboard.press("Enter")
    see(page, "#chat-body", "You wait for Ola")


def test_matters_people_and_files(win):
    page = win.open()
    win.app.assistant.memory.create_matter("Alpha launch")
    page.click("[data-tab=matters]")
    see(page, "#matters-body", "Alpha launch")
    page.fill("#matters-bar [name=note]", "Budget is fixed")
    page.click("#matters-bar form button:not([data-mic])")
    see(page, "#matters-body", "Budget is fixed")
    page.click("#matters-tabs [data-seg=files]")
    page.locator("#matters-rows .prow", has_text="alpha-launch").click()
    page.fill("#mem-text", "# Alpha launch\n\nmine")
    page.click("#matters-tabs [data-seg=people]")                  # leaving saves what was written
    see(page, ".toasts", "Saved")
    assert "mine" in win.app.assistant.memory.read("matters/alpha-launch.md")


def test_meeting_card(win):
    page = win.open()
    win.update()
    page.click("[data-tab=meetings]")
    page.click("[data-m=make]")
    see(page, "#meet-body", "Get a date for the signed contract")
    page.click("#meet-tabs [data-seg=rec]")
    see(page, "#meet-recorded", "Recorded meetings")


def test_every_tab_opens_and_fits_a_small_window(win):
    page = win.open()
    page.set_viewport_size({"width": 1000, "height": 680})
    page.click("#more")
    for tab in ("recap", "matters", "meetings", "dictation", "writing", "teams", "mail", "jira", "org", "ai", "settings", "diagnostics"):
        page.click(f"[data-tab={tab}]")
        page.wait_for_timeout(250)
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), tab
        sync.expect(page.locator("#engine-foot")).to_be_in_viewport()


def test_the_version_opens_about(win):
    page = win.open()
    page.click("#engine-foot")
    sync.expect(page.locator("#about-version")).to_have_text(re.compile(r"\d+\.\d+\.\d+"))


def test_a_question_answered_in_teams_closes_by_itself(win):
    page = win.open()
    win.update()
    see(page, "#today-rows", "Answer Jan")
    win.g.chat_msgs.append(demo.my_answer())
    page.click("[data-do=check]")
    sync.expect(page.locator("#today-rows .foldbtn", has_text="Answered")).to_be_visible(timeout=15000)
    sync.expect(page.locator("#today-rows")).not_to_contain_text("Answer Jan")
    page.locator("#today-rows .foldbtn", has_text="Answered").click()
    win.row("Answer Jan")
    page.click("[data-do=reopen]")
    sync.expect(page.locator("#today-rows .foldbtn", has_text="Answered")).to_have_count(0)


def test_an_update_can_be_stopped(win):
    page = win.open()
    real = win.app.assistant.sorting.classify
    win.app.assistant.sorting.classify = lambda *a, **k: (time.sleep(0.4), real(*a, **k))[1]
    page.select_option("#recap-range", "30")
    page.click("#recap-update")
    sync.expect(page.locator("#recap-update")).to_have_text("Stop")
    page.click("#recap-update")
    sync.expect(page.locator("#recap-update")).to_have_text("Start my day", timeout=15000)
    assert win.app.assistant.updates.job.state == "stopped"


def test_what_is_left_of_the_credits_is_in_the_menu(win):
    page = win.open()
    win.app.llm.cloud.days_left = lambda: 213
    win.app.llm.cloud.quota = lambda refresh=False: {"left": 2564.6, "entitlement": 4000, "used_pct": 35.9, "unit": "AI credits", "spent_then": 0.0,
                                                     "days_left": 6, "work_days_left": 4, "plan": "business", "unlimited": False, "checked": 0}
    win.app.settings.update({"ai_provider": "copilot"})
    see(page, "#side-left", "2,565 of 4,000 left")
    see(page, "#side-reset", "new in 6 days (4 working)")
    see(page, "#side-pct", "36% used")
    page.set_viewport_size({"width": 1000, "height": 680})
    page.click("#more")
    sync.expect(page.locator("#engine-foot")).to_be_in_viewport()


def test_a_window_without_the_key_shows_nothing_and_asks_the_app(win, browser):
    page = win.open()
    opened = []
    win.app.open_window = lambda fresh=False: opened.append(fresh)
    stranger = browser.new_context().new_page()
    stranger.goto(page.url)
    see(stranger, "body", "opens in a new window")
    stranger.wait_for_timeout(500)
    assert opened == [True] and "Morning brief" not in stranger.content()
    stranger.goto(f"{page.url}?t={win.app.door.key}")
    see(stranger, "#today-rows", "Morning brief")
    assert "t=" not in stranger.url
    stranger.context.close()


def test_feedback_is_a_file_and_the_report_can_be_read_first(win, tmp_path, monkeypatch):
    from powertools import feedback
    monkeypatch.setattr(feedback, "desktop", lambda: tmp_path)
    page = win.open()
    page.click("#idea")
    page.click("#idea-box summary")
    see(page, "#idea-report", '"version"')
    page.fill("#idea-box textarea", "Bigger buttons")
    page.click("#idea-box .primary")
    see(page, "#idea-done", "powertools-feedback-")
    assert list(tmp_path.glob("powertools-feedback-*.zip"))
    sync.expect(page.locator("#idea-github")).to_be_hidden()


def test_the_version_before_the_update_is_one_click_away(win, monkeypatch):
    from powertools import updater
    monkeypatch.setattr(updater, "previous", lambda: {"commit": "abc", "version": "0.16.0"})
    page = win.open()
    page.click("#engine-foot")
    sync.expect(page.locator("#about-back")).to_have_text("Back to 0.16.0")


def test_this_is_wrong_later_and_a_promise(win):
    page = win.open()
    win.update()
    win.row("Confirm the migration window")
    page.click("[data-do=wrong]")
    page.click("[data-do=category][data-value=fyi]")
    see(page, ".toasts", "Moved")
    win.row("Send the minutes")
    see(page, "#today-body", "You promised it")
    page.click("[data-do=p-done]")
    sync.expect(page.locator("#today-rows")).not_to_contain_text("Send the minutes")
    page.locator(".toast button").last.click()
    see(page, "#today-rows", "Send the minutes")
    win.row("Choose option A or B")
    page.click("#today-bar details.menu summary")
    page.click("[data-do=snooze][data-days='3']")
    sync.expect(page.locator("#today-rows")).not_to_contain_text("Choose option A or B")


def test_a_suggested_matter_is_created_and_leads_back_to_its_item(win):
    page = win.open()
    win.update()
    page.click("[data-do=new_matter]")
    page.click("[data-tab=matters]")
    see(page, "#matters-body", "Contract renewal")
    page.locator("#matters-body [data-do=go]").first.click()
    see(page, "#today-body", "signed contract")
    page.click("[data-tab=matters]")
    page.click("#matters-bar [data-st=parked]")
    page.click("#matters-tabs [data-seg=off]")
    see(page, "#matters-rows", "Contract renewal")


def test_a_brief_and_an_agenda_become_texts_of_mine(win):
    page = win.open()
    win.update()
    page.click("[data-kind=week]")
    page.click("[data-do=brief-draft]")
    sync.expect(page.locator("#draft-text")).to_have_value(re.compile("Finance needs"))
    page.click("[data-tab=meetings]")
    page.click("[data-m=make]")
    page.click("[data-m=agenda]")
    sync.expect(page.locator("#draft-text")).to_have_value(re.compile("Agenda:"))
    page.click("#today-bar [data-do=used]")
    see(page, "#today-rows", "The week")


def test_what_i_say_changes_a_file_and_can_be_undone(win):
    page = win.open()
    win.update()
    page.keyboard.press("Control+k")
    page.fill("#chat-q", "The contract deadline is Thursday")
    page.keyboard.press("Enter")
    see(page, "#chat-body .edit", "profile.md")
    assert "Thursday" in win.app.assistant.memory.read("profile.md")
    page.click("#chat-body [data-c=undo]")
    see(page, ".toasts", "Undone in profile.md")
    assert "Thursday" not in win.app.assistant.memory.read("profile.md")


def test_rewriting_a_draft_offers_a_rule(win):
    page = win.open()
    win.update()
    win.row("Answer Jan")
    page.fill("#today-body [name=remark]", "no greeting")
    page.click("#today-body .fixline button:not([data-mic])")
    sync.expect(page.locator("#draft-text")).to_have_value("Jan, 3 pm works.")
    page.click("#today-body .rule button")
    see(page, "#today-body .rule", "Remembered")


def test_a_one_to_one_with_a_person(win):
    page = win.open()
    win.update()
    page.click("[data-tab=matters]")
    page.click("#matters-tabs [data-seg=people]")
    page.fill("#matters-new [name=name]", "Jan")
    page.click("#matters-new button")
    see(page, "#matters-body", "Jan")
    page.fill("#matters-bar [name=note]", "likes short mails")
    page.click("#matters-bar form button:not([data-mic])")
    see(page, "#matters-body", "likes short mails")
    page.click("[data-one]")
    see(page, "#meet-rows", "1:1 with Jan")
    page.click("[data-m=file]")
    sync.expect(page.locator("#mem-text")).to_have_value(re.compile("1:1 with Jan"))


def test_start_my_day_is_one_click_to_the_brief(win):
    page = win.open()
    win.app.graph.tokens.clear()
    page.reload()
    fetched = []
    page.route("**/api/graph/token/fetch", lambda route: (fetched.append(1), route.fulfill(json={"active": True})))
    sync.expect(page.locator("#recap-update")).to_have_text("Start my day")
    page.click("#recap-update")
    see(page, "#today-body", "One step")
    assert fetched                                                           # Graph Explorer opened without another click
    page.click("#token-paste summary")
    page.fill("#token-form [name=token]", demo.token(win.app))
    win.app.graph.tokens.clear()
    page.click("#token-form button")
    see(page, "#today-body", "Finance needs your choice")
    sync.expect(page.locator("#recap-update")).to_have_text("Update")
    page.click("[data-do=brief-close]")
    see(page, "#today-body", "First these three")


def test_three_questions_of_one_chat_are_three_rows_that_know_each_other(win):
    page = win.open()
    win.update()
    win.g.chat_msgs.append({**demo.my_answer(), "id": "t3", "from": {"user": {"id": "u-jan", "displayName": "Jan"}},
                            "body": {"contentType": "text", "content": "THREE THINGS: the room, the agenda and the window?"}})
    page.click("#recap-update")
    for text in ("Book the room", "Send the agenda", "Confirm the window with Jan"):
        see(page, "#today-rows", text)
    row = page.locator("#today-rows .prow", has_text="Send the agenda")
    sync.expect(row.locator(".dot")).to_have_count(1)
    row.click()
    sync.expect(row.locator(".dot")).to_have_count(0)
    page.locator("#today-body [data-do=row]", has_text="Book the room").click()
    see(page, "#today-body h2", "Book the room")
    page.click("[data-do=seen]")
    sync.expect(page.locator("#today-rows .dot")).to_have_count(0)


# ---------------------------------------------------------------- the chat
def test_the_chat_stays_on_every_tab_grows_and_remembers(win):
    page = win.open()
    win.update()
    page.click("#chat-open")
    page.fill("#chat-q", "Who am I waiting for?")
    page.click("#chat-send")
    see(page, "#chat-body", "You wait for Ola")
    for tab in ("matters", "meetings", "dictation"):
        page.click(f"[data-tab={tab}]")
        sync.expect(page.locator("#chat")).to_be_visible()
    small = page.locator("#chat").bounding_box()
    page.click("#chat-big")
    assert page.locator("#chat").bounding_box()["height"] > small["height"] + 50
    page.reload()
    sync.expect(page.locator("#chat.big")).to_be_visible()                     # open and large, as left
    see(page, "#chat-body", "You wait for Ola")
    page.locator(".turn").first.hover()
    page.click(".turn [data-c=again]")
    sync.expect(page.locator("#chat-q")).to_have_value("Who am I waiting for?")
    page.click(".turn [data-c=del]")
    sync.expect(page.locator(".turn")).to_have_count(0)
    page.locator("#chat-q").press("Escape")
    sync.expect(page.locator("#chat")).to_be_hidden()
    sync.expect(page.locator("#chat-open")).to_be_visible()


def test_a_question_is_about_the_open_item_unless_i_say_not(win):
    page = win.open()
    win.update()
    win.row("Confirm the migration window")
    page.click("#chat-open")
    see(page, "#chat-about", "Confirm the migration window")
    page.fill("#chat-q", "What do they want?")
    page.keyboard.press("Enter")
    see(page, "#chat-body .q small", "Question about the migration window")
    page.click("[data-c=nocontext]")
    sync.expect(page.locator("#chat-about")).to_be_empty()


def test_an_answer_can_be_stopped(win):
    page = win.open()
    win.update()
    ask = win.app.assistant.asking.ask
    win.app.assistant.asking.ask = lambda *a: (time.sleep(1.2), ask(*a))[1]
    page.click("#chat-open")
    page.fill("#chat-q", "Anything?")
    page.keyboard.press("Enter")
    sync.expect(page.locator("#chat-send")).to_have_text("Stop")
    see(page, "#chat-body", "Thinking")
    sync.expect(page.locator("#chat-send")).to_have_text("Ask", timeout=8000)


def test_the_question_can_be_dictated(win):
    page = win.open()
    page.click("#chat-open")
    page.fill("#chat-q", "Please note:")
    mic = page.locator("#chat-form [data-mic]")
    mic.click()
    sync.expect(mic).to_have_attribute("aria-pressed", "true")
    mic.click()
    sync.expect(page.locator("#chat-q")).to_have_value("Please note: the deadline is Thursday")
    sync.expect(mic).to_have_attribute("aria-pressed", "false")
    assert win.app.pasted == []


def test_the_chat_does_not_cover_the_buttons_of_an_item(win):
    page = win.open()
    win.update()
    win.row("Confirm the migration window")
    wrong, fab = page.locator("#today-bar [data-do=wrong]").bounding_box(), page.locator("#chat-open").bounding_box()
    assert wrong["x"] + wrong["width"] <= fab["x"]


# ---------------------------------------------------------------- deleting and editing
def test_an_item_is_edited_and_deleted_with_undo(win):
    page = win.open()
    win.update()
    win.row("Confirm the migration window")
    page.click("#today-bar [data-k=more]")
    page.click("#today-bar [data-do=edit]")
    page.fill("[data-edit] [name=action]", "Confirm the second window")
    page.fill("[data-edit] [name=due]", "Friday")
    page.click("[data-edit] .primary")
    see(page, "#today-rows", "Confirm the second window")
    win.row("Confirm the second window")
    page.click("#today-bar [data-k=more]")
    page.click("#today-bar [data-do=delete]")
    sync.expect(page.locator("#today-rows")).not_to_contain_text("Confirm the second window")
    page.locator(".toast button").last.click()
    see(page, "#today-rows", "Confirm the second window")


def test_a_promise_is_edited(win):
    page = win.open()
    win.update()
    win.row("Send the minutes")
    page.click("[data-do=edit]")
    page.fill("[data-edit] [name=action]", "send the long minutes")
    page.click("[data-edit] .primary")
    see(page, "#today-rows", "Send the long minutes")


def test_lines_of_a_matter_are_edited_and_deleted_and_the_matter_too(win):
    page = win.open()
    win.app.assistant.memory.create_matter("Alpha launch")
    page.click("[data-tab=matters]")
    page.fill("#matters-bar [name=note]", "Budget is fixed")
    page.click("#matters-bar form button:has-text('Add')")
    line = page.locator(".eline", has_text="Budget is fixed")
    line.hover()
    line.locator("[data-tool=edit]").click()
    page.fill(".eline [name=line]", "Budget is open")
    page.click(".eline form button:has-text('Save')")
    see(page, "#matters-body", "Budget is open")
    line = page.locator(".eline", has_text="Budget is open")
    line.hover()
    line.locator("[data-tool=del]").click()
    sync.expect(page.locator("#matters-body")).not_to_contain_text("Budget is open")
    page.locator(".toast button").last.click()
    see(page, "#matters-body", "Budget is open")
    page.click("#matters-bar [data-k=more]")
    page.click("#matters-bar [data-rename]")
    page.fill("#matters-body [data-rename] input", "Beta launch")
    page.click("#matters-body [data-rename] button")
    see(page, "#matters-rows", "Beta launch")
    page.click("#matters-bar [data-k=more]")
    page.click("#matters-bar [data-del]")
    see(page, "#matters-rows", "No matters yet")
    page.locator(".toast button").last.click()
    see(page, "#matters-rows", "Beta launch")


def test_texts_drafts_people_and_cards_can_be_thrown_away_and_come_back(win):
    page = win.open()
    win.update()
    win.row("Confirm the migration window")
    page.click("[data-do=drop]")
    sync.expect(page.locator(".draftcard")).to_have_count(0)
    page.locator(".toast button").last.click()
    sync.expect(page.locator(".draftcard")).to_have_count(1)
    page.click("[data-tab=matters]")
    page.click("#matters-tabs [data-seg=people]")
    page.click("[data-rm]")
    see(page, "#matters-rows", "Nobody yet")
    page.locator(".toast button").last.click()
    see(page, "#matters-rows", "Ola")
    page.click("[data-tab=meetings]")
    page.click("[data-m=make]")
    see(page, "#meet-body", "Get a date")
    page.click("[data-m=drop]")
    see(page, "#meet-body", "No card yet")


def test_the_dictation_history_is_edited_and_emptied(win):
    page = win.open()
    page.click("[data-tab=dictation]")
    row = page.locator("#history li").first
    row.hover()
    row.locator("[data-act=edit]").click()
    page.fill("#history form.edit textarea", "in my words")
    page.click("#history form.edit button")
    see(page, "#history", "in my words")
    page.click("#history-clear")
    page.click("[data-sure='0']")
    see(page, "#history", "Nothing yet")


def test_what_it_learned_is_shown_and_can_be_taken_back(win):
    page = win.open()
    win.update()
    win.row("Confirm the migration window")
    page.click("[data-do=wrong]")
    page.click("[data-do=category][data-value=fyi]")
    page.click("#more")
    page.click("[data-tab=ai]")
    see(page, "#ai-learned", "Question about the migration window")
    page.locator("#ai-learned li").hover()
    page.click("[data-forget]")
    see(page, "#ai-learned", "Nothing yet")


def test_a_recording_is_renamed_and_deleted(win, tmp_path):
    from powertools.meetings.manager import Session
    page = win.open()
    folder = win.app.meetings.root / "20261125-100000"
    folder.mkdir()
    Session(folder, {"title": "Teams call", "started": time.time(), "status": "done"}).save()
    page.click("[data-tab=meetings]")
    page.click("#meet-tabs [data-seg=rec]")
    page.click("#meeting-list [data-act=name]")
    page.fill("#meeting-list [name=title]", "Contract sync")
    page.click("#meeting-list form button")
    see(page, "#meeting-list", "Contract sync")
    page.click("#meeting-list [data-act=del]")
    page.click("[data-sure='0']")
    see(page, "#meeting-list", "No recordings yet")


def test_a_working_button_is_stop(win):
    page = win.open()
    win.update()
    make = win.app.assistant.briefs.prep
    win.app.assistant.briefs.prep = lambda *a: (time.sleep(1), make(*a))[1]
    page.click("[data-tab=meetings]")
    page.click("[data-m=make]")
    sync.expect(page.locator("[data-m=make]")).to_have_text("Stop")
    stopped = []
    win.app.llm.abort = lambda: stopped.append(1)
    page.click("[data-m=make]")
    see(page, "#meet-body", "Get a date")
    assert stopped
