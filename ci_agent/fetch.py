"""Download web pages politely and turn them into clean text."""

from __future__ import annotations

import time
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import httpx
import trafilatura

USER_AGENT = "competitor-intel-agent/0.1 (+https://github.com/lucagalvani/competitor-intel-agent)"
TIMEOUT = 30.0
DELAY_BETWEEN_REQUESTS = 1.0  # seconds, per host — don't hammer anyone's site

_robots_cache: dict[str, RobotFileParser | None] = {}
_last_request: dict[str, float] = {}


class FetchError(RuntimeError):
    pass


def allowed_by_robots(url: str) -> bool:
    """Check the site's robots.txt. If it can't be read, assume allowed."""
    parts = urlparse(url)
    host = f"{parts.scheme}://{parts.netloc}"
    if host not in _robots_cache:
        parser = RobotFileParser()
        try:
            resp = httpx.get(f"{host}/robots.txt", headers={"User-Agent": USER_AGENT}, timeout=10, follow_redirects=True)
            parser.parse(resp.text.splitlines() if resp.status_code == 200 else [])
            _robots_cache[host] = parser
        except httpx.HTTPError:
            _robots_cache[host] = None
    parser = _robots_cache[host]
    return parser is None or parser.can_fetch(USER_AGENT, url)


def _wait_for_host(url: str) -> None:
    host = urlparse(url).netloc
    elapsed = time.monotonic() - _last_request.get(host, 0)
    if elapsed < DELAY_BETWEEN_REQUESTS:
        time.sleep(DELAY_BETWEEN_REQUESTS - elapsed)
    _last_request[host] = time.monotonic()


def fetch_html(url: str, render: bool = False) -> str:
    if not allowed_by_robots(url):
        raise FetchError(f"robots.txt disallows fetching {url}")
    _wait_for_host(url)
    if render:
        return _fetch_with_browser(url)
    try:
        resp = httpx.get(url, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT, follow_redirects=True)
        resp.raise_for_status()
    except httpx.HTTPError as e:
        raise FetchError(f"could not fetch {url}: {e}") from e
    return resp.text


def _fetch_with_browser(url: str) -> str:
    """For JavaScript-heavy pages: load them in a headless browser first."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:
        raise FetchError(
            "`render: true` needs Playwright: pip install '.[browser]' && playwright install chromium"
        ) from e
    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page(user_agent=USER_AGENT)
            page.goto(url, wait_until="networkidle", timeout=TIMEOUT * 1000)
            return page.content()
        finally:
            browser.close()


def html_to_text(html: str) -> str:
    """Keep the meaningful content (headings, copy, tables), drop menus, footers and cookie banners."""
    text = trafilatura.extract(
        html,
        output_format="markdown",
        include_tables=True,
        include_links=False,
        include_comments=False,
        favor_recall=True,  # pricing pages are often table-heavy; don't throw them away
    )
    if not text:
        # Fallback for unusual pages: plain text of the whole document
        text = trafilatura.html2txt(html) or ""
    return text


def fetch_page_text(url: str, render: bool = False) -> str:
    text = html_to_text(fetch_html(url, render=render))
    if not text.strip():
        raise FetchError(f"no readable content at {url} (try `render: true` if the site uses JavaScript)")
    return text
