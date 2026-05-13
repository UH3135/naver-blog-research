"""Naver blog content fetch tool."""

import re
from html import unescape
from typing import Any
from urllib.parse import urljoin
from urllib.request import Request, urlopen

from langchain_core.tools import tool
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
)

from app.core.logging import logger

REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
}


def _strip_html(value: str) -> str:
    """Remove tags and normalize whitespace."""
    tag_stripped = re.sub(r"<[^>]+>", " ", value)
    whitespace_normalized = re.sub(r"\s+", " ", tag_stripped)
    return unescape(whitespace_normalized).strip()


def _extract_iframe_url(html: str, base_url: str) -> str | None:
    """Extract the post iframe URL from the outer blog page."""
    match = re.search(r'<iframe[^>]+id="mainFrame"[^>]+src="(?P<src>[^"]+)"', html)
    if not match:
        return None
    return urljoin(base_url, match.group("src"))


def _extract_title(html: str) -> str:
    """Extract a readable title from the HTML."""
    for pattern in [
        r'<meta property="og:title" content="(?P<value>[^"]+)"',
        r"<title>(?P<value>.*?)</title>",
    ]:
        match = re.search(pattern, html, re.IGNORECASE | re.DOTALL)
        if match:
            return _strip_html(match.group("value"))
    return ""


def _extract_published_at(html: str) -> str:
    """Extract a published date string if available."""
    patterns = [
        r'<meta property="article:published_time" content="(?P<value>[^"]+)"',
        r'<span[^>]+class="[^"]*(?:se_publishDate|date|publish)[^"]*"[^>]*>(?P<value>.*?)</span>',
    ]
    for pattern in patterns:
        match = re.search(pattern, html, re.IGNORECASE | re.DOTALL)
        if match:
            return _strip_html(match.group("value"))
    return ""


def _extract_body_text(html: str) -> str:
    """Extract readable body text from common Naver blog containers."""
    patterns = [
        r'<div[^>]+class="[^"]*se-main-container[^"]*"[^>]*>(?P<value>.*?)</div>',
        r'<div[^>]+id="postViewArea"[^>]*>(?P<value>.*?)</div>',
        r'<div[^>]+class="[^"]*post-view[^"]*"[^>]*>(?P<value>.*?)</div>',
    ]
    for pattern in patterns:
        match = re.search(pattern, html, re.IGNORECASE | re.DOTALL)
        if match:
            return _strip_html(match.group("value"))
    return _strip_html(html)


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=4), reraise=True)
def _load_html(url: str) -> str:
    """Load a page as HTML."""
    request = Request(url, headers=REQUEST_HEADERS)
    with urlopen(request, timeout=10) as response:  # noqa: S310
        return response.read().decode("utf-8", errors="ignore")


@tool
def fetch_naver_blog_content(url: str) -> dict[str, Any]:
    """Fetch blog content preview for a Naver blog post."""
    logger.info("naver_blog_content_fetch_started", url=url)
    html = _load_html(url)
    iframe_url = _extract_iframe_url(html, url)
    if iframe_url:
        html = _load_html(iframe_url)

    title = _extract_title(html)
    raw_text = _extract_body_text(html)
    published_at = _extract_published_at(html)
    excerpt = raw_text[:300]

    result = {
        "title": title,
        "url": url,
        "published_at": published_at,
        "raw_text": raw_text,
        "excerpt": excerpt,
        "fetch_status": "success" if raw_text else "failed",
    }
    logger.info("naver_blog_content_fetch_completed", url=url, fetch_status=result["fetch_status"])
    return result
