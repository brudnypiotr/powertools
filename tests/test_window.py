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
    see(page, "#today-bar", "Put the new text into Outlook drafts")


def test_a_failed_update_says_why(win):
    page = win.open()
    win.update()
    win.app.assistant.updates.job.state, win.app.assistant.updates.job.error = "error", "no connection to Microsoft"
    page.reload()
    see(page, "#today-rows", "no connection to Microsoft")


def test_ask_answers_and_remembers(win):
    page = win.open()
    win.update()
    page.fill("#ask-form input", "Who am I waiting for?")
    page.keyboard.press("Enter")
    see(page, "#today-body", "You wait for Ola")


def test_matters_people_and_files(win):
    page = win.open()
    win.app.assistant.memory.create_matter("Alpha launch")
    page.click("[data-tab=matters]")
    see(page, "#matters-body", "Alpha launch")
    page.fill("#matters-bar [name=note]", "Budget is fixed")
    page.click("#matters-bar form button")
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
    page.fill("#ask-form input", "The contract deadline is Thursday")
    page.keyboard.press("Enter")
    see(page, "#today-body .edit", "profile.md")
    assert "Thursday" in win.app.assistant.memory.read("profile.md")
    page.click("[data-do=mem-undo]")
    see(page, ".toasts", "Undone in profile.md")
    assert "Thursday" not in win.app.assistant.memory.read("profile.md")


def test_rewriting_a_draft_offers_a_rule(win):
    page = win.open()
    win.update()
    win.row("Answer Jan")
    page.fill("#today-body [name=remark]", "no greeting")
    page.click("#today-body .fixline button")
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
    page.click("#matters-bar form button")
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
