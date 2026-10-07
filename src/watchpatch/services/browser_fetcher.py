"""Render public pages with a disposable browser context when static HTML is insufficient."""

import logging
import os
import shutil
from pathlib import Path

from watchpatch.services.fetcher import FetchError, validate_url

logger = logging.getLogger(__name__)


def _installed_browser() -> str | None:
    """Find a configured or commonly installed Chromium compatible browser."""
    configured = os.environ.get("WATCHPATCH_BROWSER_EXECUTABLE")
    candidates = [configured] if configured else []
    candidates.extend(
        [
            shutil.which("chrome"),
            shutil.which("msedge"),
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        ]
    )
    return next(
        (candidate for candidate in candidates if candidate and Path(candidate).is_file()), None
    )


async def fetch_rendered_html(url: str) -> str:
    validate_url(url)
    try:
        from playwright.async_api import Error as PlaywrightError
        from playwright.async_api import async_playwright
    except ImportError as exc:
        raise FetchError(
            "抖音页面需要浏览器渲染。请安装支持：pip install -e \".[browser]\"，"
            "然后运行 python -m playwright install chromium。"
        ) from exc

    try:
        async with async_playwright() as playwright:
            launch_options = {"headless": True}
            executable = (
                None
                if Path(playwright.chromium.executable_path).is_file()
                else _installed_browser()
            )
            if executable is not None:
                launch_options["executable_path"] = executable
                logger.info("使用本机浏览器渲染: %s", executable)
            browser = await playwright.chromium.launch(**launch_options)
            try:
                context = await browser.new_context()
                page = await context.new_page()
                response = await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                if response is None:
                    raise FetchError("浏览器未收到网页响应，保留原有快照。")
                if response.status >= 400:
                    raise FetchError(f"网页返回 HTTP {response.status}，保留原有快照。")
                await page.wait_for_timeout(1500)
                html = await page.content()
                if not html.strip():
                    raise FetchError("渲染后的网页内容为空，保留原有快照。")
                return html
            finally:
                await browser.close()
    except FetchError:
        raise
    except PlaywrightError as exc:
        logger.warning("浏览器渲染失败: %s", exc)
        if "ERR_NETWORK_ACCESS_DENIED" in str(exc):
            raise FetchError("浏览器网络访问被拒绝，请检查网络、代理或安全软件设置。") from exc
        raise FetchError(
            "浏览器渲染失败。请安装 Chromium（python -m playwright install chromium），"
            "或设置 WATCHPATCH_BROWSER_EXECUTABLE 指向 Chrome/Edge；并确认公开页面可正常访问。"
        ) from exc
