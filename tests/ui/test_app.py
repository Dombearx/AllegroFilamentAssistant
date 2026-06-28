"""
Browser-level tests for the NiceGUI UI.

Requires the live_server_url fixture (starts the app on port 8765 with test
credentials).  Allegro API calls will fail (invalid credentials), so the
"Could not load filters" warning is expected on the main page.
"""
import pytest
from playwright.sync_api import Page, expect

TIMEOUT = 15_000  # ms — NiceGUI hydrates via WebSocket, allow extra time


@pytest.mark.usefixtures("live_server_url")
class TestMainPage:
    def test_page_title(self, page: Page, live_server_url: str):
        page.goto(live_server_url, wait_until="networkidle")
        expect(page).to_have_title("Filament Color Finder", timeout=TIMEOUT)

    def test_header_visible(self, page: Page, live_server_url: str):
        page.goto(live_server_url, wait_until="networkidle")
        expect(page.get_by_text("Filament Color Finder").first).to_be_visible(
            timeout=TIMEOUT
        )

    def test_find_button_visible(self, page: Page, live_server_url: str):
        page.goto(live_server_url, wait_until="networkidle")
        expect(page.get_by_text("Find matching filaments")).to_be_visible(timeout=TIMEOUT)

    def test_dev_link_visible(self, page: Page, live_server_url: str):
        page.goto(live_server_url, wait_until="networkidle")
        expect(page.get_by_text("⚙ Dev")).to_be_visible(timeout=TIMEOUT)

    def test_navigates_to_dev_page(self, page: Page, live_server_url: str):
        page.goto(live_server_url, wait_until="networkidle")
        page.get_by_text("⚙ Dev").click()
        expect(page).to_have_url(f"{live_server_url}/dev", timeout=TIMEOUT)
        expect(page.get_by_text("Dev Settings")).to_be_visible(timeout=TIMEOUT)


@pytest.mark.usefixtures("live_server_url")
class TestDevPage:
    def test_dev_page_title(self, page: Page, live_server_url: str):
        page.goto(f"{live_server_url}/dev", wait_until="networkidle")
        expect(page).to_have_title(
            "Dev Settings — Filament Assistant", timeout=TIMEOUT
        )

    def test_refresh_filters_button(self, page: Page, live_server_url: str):
        page.goto(f"{live_server_url}/dev", wait_until="networkidle")
        expect(page.get_by_text("Refresh filters")).to_be_visible(timeout=TIMEOUT)

    def test_clear_category_button(self, page: Page, live_server_url: str):
        page.goto(f"{live_server_url}/dev", wait_until="networkidle")
        expect(page.get_by_text("Clear category cache")).to_be_visible(timeout=TIMEOUT)

    def test_back_button_navigates_home(self, page: Page, live_server_url: str):
        page.goto(f"{live_server_url}/dev", wait_until="networkidle")
        page.get_by_text("← Back").click()
        expect(page).to_have_url(f"{live_server_url}/", timeout=TIMEOUT)

    def test_allegro_env_shown(self, page: Page, live_server_url: str):
        page.goto(f"{live_server_url}/dev", wait_until="networkidle")
        # Environment label shows the exact value without surrounding text
        expect(page.get_by_text("sandbox", exact=True).first).to_be_visible(timeout=TIMEOUT)
