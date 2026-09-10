import json
import re

from playwright.sync_api import expect

from ui_regression_support import capture_evidence
import pytest


# Needs a real browser: these drive Chromium through the `page`/`browser_session`
# fixtures and start a built Studio server. Marked so the default suite excludes them
# rather than failing on a machine without Playwright's browsers installed -- CI runs
# `-m "not external and not browser and not android"` and, unmarked, these were inside
# that selection while nothing in the workflow ever ran `playwright install`.
pytestmark = pytest.mark.browser


def _open_studio(page, studio_server):
    page.goto(studio_server, wait_until="networkidle")
    onboarding_close = page.get_by_role("button", name="Close this window", exact=True)
    if onboarding_close.count():
        onboarding_close.click()
    expect(page.locator(".fs-shell")).to_be_visible()


def _open_settings(page, tab="appearance"):
    onboarding_close = page.get_by_role("button", name="Close this window", exact=True)
    if onboarding_close.count():
        onboarding_close.click()
    page.locator('.fs-header-actions button[title="Open settings"]').click()
    body = page.get_by_role("region", name="Settings content")
    expect(body).to_be_visible()
    page.locator(f'[data-settings-tab="{tab}"]').click()
    expect(body).to_have_attribute("data-active-tab", tab)
    return body


def _set_theme(page, theme):
    _open_settings(page, "appearance")
    label = "Light" if theme == "light" else "Dark"
    with page.expect_response(
        lambda response: response.request.method == "POST" and "/api/config/system" in response.url
    ) as response_info:
        page.get_by_role("button", name=re.compile(rf"{label}$")).click()
    assert response_info.value.ok
    page.wait_for_function(
        "theme => document.documentElement.classList.contains('theme-light') === (theme === 'light')",
        arg=theme,
    )


def _screenshot(page, theme, screen):
    capture_evidence(page, f"{theme}_{screen}")


def _style(locator):
    return locator.evaluate(
        """element => {
            const style = getComputedStyle(element);
            return {
                color: style.color,
                backgroundColor: style.backgroundColor,
                backgroundImage: style.backgroundImage,
                borderColor: style.borderColor,
                opacity: style.opacity,
                outlineColor: style.outlineColor,
                outlineStyle: style.outlineStyle,
                scrollbarColor: style.scrollbarColor,
            };
        }"""
    )


def _contrast_report(page):
    # The colour is painted and read back, rather than parsed out of the token text.
    #
    # The parser this replaces took the first three numbers it found and divided them by
    # 255, which is right for `#rrggbb` and `rgb(r, g, b)` and nonsense for anything else.
    # When the Liquid Glass redesign moved every token to `oklch()`, it began reading
    # `oklch(0.23 0.012 300)` as the channels 0.23, 0.012 and 300 -- and reported the light
    # theme's body text at 3.09 against white, which failed the 4.5 assertion below and read
    # exactly like a real accessibility regression. It is not one: the same pair measured
    # through a canvas is 16.88. Verified 2026-09-10 by running both parsers side by side on
    # the same two colours; the old one reproduced 3.0894088268980315 to the digit, and both
    # agree that black on white is 21.
    #
    # A canvas has no opinion about notation. Whatever the browser can paint, it can paint
    # into one pixel, and the pixel comes back as sRGB bytes. `getComputedStyle().color` is
    # not a substitute -- it hands back `oklch(...)` unchanged, which is the trap again.
    return page.evaluate(
        """() => {
            const probe = document.createElement('canvas');
            probe.width = probe.height = 1;
            const ctx = probe.getContext('2d', { willReadFrequently: true });
            const parse = value => {
                ctx.clearRect(0, 0, 1, 1);
                // Painted twice: an unparseable value leaves fillStyle at its previous
                // setting, so the black underneath makes a bad token read as black rather
                // than silently inheriting the colour measured just before it.
                ctx.fillStyle = '#000000';
                ctx.fillStyle = String(value).trim();
                ctx.fillRect(0, 0, 1, 1);
                const [r, g, b] = ctx.getImageData(0, 0, 1, 1).data;
                return [r, g, b].map(channel => {
                    channel /= 255;
                    return channel <= 0.04045 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4;
                });
            };
            const luminance = value => {
                const [r, g, b] = parse(value);
                return 0.2126 * r + 0.7152 * g + 0.0722 * b;
            };
            const contrast = (a, b) => {
                const first = luminance(a);
                const second = luminance(b);
                return (Math.max(first, second) + 0.05) / (Math.min(first, second) + 0.05);
            };
            const root = getComputedStyle(document.documentElement);
            const panel = root.getPropertyValue('--bg-card').trim();
            return {
                primary: contrast(root.getPropertyValue('--text-primary'), panel),
                secondary: contrast(root.getPropertyValue('--text-secondary'), panel),
                success: contrast(root.getPropertyValue('--success'), panel),
                warning: contrast(root.getPropertyValue('--warning'), panel),
                danger: contrast(root.getPropertyValue('--danger'), panel),
                accent: contrast(root.getPropertyValue('--accent'), panel),
                fsPanel: root.getPropertyValue('--fs-panel-strong').trim(),
                fsText: root.getPropertyValue('--fs-text').trim(),
                // Asserted by the caller before anything else is read. The failure this
                // guards against is not a wrong colour, it is a measurement that quietly
                // stops measuring: the previous parser went on returning plausible numbers
                // for a year's worth of notations it could not read. A ratio that is not 21
                // means the instrument is broken, and nothing below it means anything.
                sanityBlackOnWhite: contrast('#000000', '#ffffff'),
            };
        }"""
    )


def _capture_key_screens(page, theme):
    page.locator('.fs-nav button[title="Overview"]').click()
    expect(page.locator(".fs-hero")).to_be_visible()
    _screenshot(page, theme, "dashboard")

    for tab in ("appearance", "general", "ai", "studio"):
        body = _open_settings(page, tab)
        if tab == "ai":
            expect(page.get_by_test_id("settings-last-ai")).to_be_attached()
        _screenshot(page, theme, tab)
        assert body.evaluate("element => getComputedStyle(element).overflowY") == "auto"

    page.locator(".fs-about-button").click()
    expect(page.get_by_role("region", name="Info content")).to_be_visible()
    _screenshot(page, theme, "info")


def test_light_theme_runtime_contrast_screenshots_and_persistence(studio_server, browser_session):
    with browser_session(viewport={"width": 1366, "height": 768}) as page:
        _open_studio(page, studio_server)

        _set_theme(page, "dark")
        _capture_key_screens(page, "dark")

        _set_theme(page, "light")
        assert page.evaluate("localStorage.getItem('studio_theme')") == "light"
        light_report = _contrast_report(page)
        assert abs(light_report["sanityBlackOnWhite"] - 21) < 0.01, (
            "the contrast probe itself is broken; every ratio below this line is meaningless",
            light_report["sanityBlackOnWhite"],
        )
        for state in ("primary", "secondary", "success", "warning", "danger", "accent"):
            assert light_report[state] >= 4.5, (state, light_report[state])

        settings_panel = _style(page.locator(".settings-inline-container"))
        settings_body = _style(page.get_by_role("region", name="Settings content"))
        assert settings_panel["backgroundColor"] == "rgb(255, 255, 255)"
        assert settings_body["color"] != "rgb(255, 255, 255)"

        theme_button = page.get_by_role("button", name=re.compile(r"Light$"))
        button_style = _style(theme_button)
        assert button_style["color"] != "rgb(255, 255, 255)"

        page.locator('[data-settings-tab="general"]').click()
        general_select = page.get_by_role("combobox").first
        general_select.focus()
        select_style = _style(general_select)
        assert select_style["backgroundColor"] in {"rgb(241, 245, 249)", "rgb(255, 255, 255)"}
        assert select_style["color"] != "rgb(255, 255, 255)"
        assert select_style["outlineStyle"] != "none"

        page.locator('[data-settings-tab="ai"]').click()
        expect(page.get_by_test_id("settings-last-ai")).to_be_attached()
        ai_input = page.locator(".provider-form input").first
        ai_disabled = page.locator(".provider-actions button:disabled").first
        ai_input_style = _style(ai_input)
        disabled_style = _style(ai_disabled)
        assert ai_input_style["backgroundColor"] == "rgb(255, 255, 255)"
        assert ai_input_style["color"] != "rgb(255, 255, 255)"
        assert float(disabled_style["opacity"]) <= 0.5
        assert disabled_style["borderColor"] != "rgba(0, 0, 0, 0)"

        page.locator('.fs-nav button[title="Overview"]').click()
        sidebar_style = _style(page.locator(".fs-sidebar"))
        panel_style = _style(page.locator(".fs-panel").first)
        assert "rgb(4, 14, 26)" not in sidebar_style["backgroundImage"]
        assert "rgb(6, 17, 31)" not in panel_style["backgroundImage"]
        nav_button = page.locator('.fs-nav button[title="Projects"]')
        before_hover = _style(nav_button)["backgroundColor"]
        nav_button.hover()
        after_hover = _style(nav_button)["backgroundColor"]
        assert before_hover != after_hover

        _capture_key_screens(page, "light")

        info_card = _style(page.locator(".info-inline-body > div").first)
        info_text = _style(page.locator(".info-inline-body h3").first)
        info_scroller = _style(page.get_by_role("region", name="Info content"))
        assert info_card["backgroundColor"] == "rgb(255, 255, 255)"
        assert info_text["color"] != "rgb(255, 255, 255)"
        assert "auto" not in info_scroller["scrollbarColor"]

        page.locator('.fs-nav button[title="Logs"]').click()
        expect(page.locator(".fs-full-log")).to_be_visible()
        log_style = _style(page.locator(".fs-full-log"))
        status_style = _style(page.locator(".fs-status").first)
        assert log_style["color"] != "rgb(255, 255, 255)"
        assert status_style["color"] != "rgb(255, 255, 255)"

        page.reload(wait_until="domcontentloaded")
        expect(page.locator("html")).to_have_class(re.compile(r"theme-light"))
        page.wait_for_load_state("networkidle")
        assert page.evaluate("document.documentElement.classList.contains('theme-light')")

        _set_theme(page, "dark")
        assert not page.evaluate("document.documentElement.classList.contains('theme-light')")
        assert page.evaluate("localStorage.getItem('studio_theme')") == "dark"

        print("THEME_CONTRAST=" + json.dumps(light_report, sort_keys=True))
