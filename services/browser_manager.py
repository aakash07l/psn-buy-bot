"""
Browser lifecycle manager for Playwright.
Handles headless Chromium instances, session storage_state, anti-detect stealth scripts, and screenshots.
"""

import os
import json
import logging
import base64
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any
from playwright.async_api import async_playwright, Browser, BrowserContext, Page, Playwright

import config

logger = logging.getLogger(__name__)

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)


class BrowserManager:
    """Manages Playwright browser instances and user sessions."""

    def __init__(self):
        self._playwright: Optional[Playwright] = None
        self._browser: Optional[Browser] = None

    async def initialize(self):
        """Starts Playwright and launches Chromium."""
        if not self._playwright:
            self._playwright = await async_playwright().start()
        if not self._browser or not self._browser.is_connected():
            self._browser = await self._playwright.chromium.launch(
                headless=config.HEADLESS,
                args=[
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-blink-features=AutomationControlled",
                    "--disable-gpu",
                    "--disable-extensions",
                    "--disable-background-networking",
                    "--disable-default-apps",
                    "--mute-audio",
                    "--no-first-run",
                ],
            )
            logger.info("Chromium browser launched successfully.")

    def has_session(self) -> bool:
        """Checks if a valid storage state session file exists."""
        return config.STORAGE_STATE_FILE.exists()

    async def create_context(
        self,
        apply_storage_state: bool = True,
    ) -> BrowserContext:
        """Creates an authenticated or fresh BrowserContext."""
        await self.initialize()

        storage_path = None
        if apply_storage_state and config.STORAGE_STATE_FILE.exists():
            storage_path = str(config.STORAGE_STATE_FILE)
            logger.info(f"Loaded storage state from {storage_path}")

        context = await self._browser.new_context(
            storage_state=storage_path,
            user_agent=DEFAULT_USER_AGENT,
            viewport={"width": 1366, "height": 768},
            locale="en-IN",
            timezone_id="Asia/Kolkata",
        )

        # Anti-detection stealth script & Location popup auto-dismiss
        init_script = """
            Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined
            });
            window.chrome = {
                runtime: {}
            };
            try {
                setInterval(() => {
                    const btns = Array.from(document.querySelectorAll('button, a, div[role="button"]'));
                    for (const b of btns) {
                        const txt = (b.innerText || '').toLowerCase().trim();
                        if (txt.includes('stay on this site') || txt.includes('no, stay')) {
                            b.click();
                            break;
                        }
                    }
                }, 250);
            } catch (e) {}
        """
        await context.add_init_script(init_script)
        return context

    async def capture_screenshot(self, page: Page, name_prefix: str = "shot") -> str:
        """Takes a full page screenshot and saves it to SCREENSHOT_DIR."""
        try:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"{name_prefix}_{timestamp}.png"
            filepath = config.SCREENSHOT_DIR / filename
            await page.screenshot(path=str(filepath), full_page=False)
            return str(filepath)
        except Exception as e:
            logger.warning(f"Failed to capture screenshot: {e}")
            return ""

    async def close(self):
        """Closes browser and Playwright process."""
        if self._browser:
            try:
                await self._browser.close()
            except Exception:
                pass
            self._browser = None
        if self._playwright:
            try:
                await self._playwright.stop()
            except Exception:
                pass
            self._playwright = None


# Global singleton
browser_manager = BrowserManager()
