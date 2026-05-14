"""Naver blog content fetch tool."""

import re
from html import unescape
from typing import Any
from urllib.parse import (
    parse_qs,
    urlparse,
)
from urllib.request import Request, urlopen

from langchain_core.tools import tool
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
)

from app.core.logging import logger

REQUEST_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ko,en-US;q=0.9,en;q=0.8",
    "User-Agent": (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
    ),
}
TAG_RE = re.compile(r"<[^>]+>")
BR_RE = re.compile(r"<br\s*/?>", re.IGNORECASE)
BLOCK_END_RE = re.compile(r"</(p|div|li)>", re.IGNORECASE)
WHITESPACE_RE = re.compile(r"[ \t]+")
BLANK_LINES_RE = re.compile(r"\n{3,}")
SCRIPT_STYLE_RE = re.compile(r"<(script|style|noscript)[^>]*>.*?</\1>", re.DOTALL | re.IGNORECASE)
TITLE_SUFFIX_RE = re.compile(r"\s*[-:|]?\s*네이버\s*블로그$", re.IGNORECASE)
BLOG_PATH_RE = re.compile(r"^/([a-zA-Z0-9_]+)/(\d+)/?$")
NAVER_DOMAINS = (".naver.com", ".naver.net", ".pstatic.net")


def _strip_html(value: str) -> str:
    """Remove tags and normalize whitespace."""
    tag_stripped = re.sub(r"<[^>]+>", " ", value)
    whitespace_normalized = re.sub(r"\s+", " ", tag_stripped)
    return unescape(whitespace_normalized).strip()


def _is_naver_url(url: str) -> bool:
    """Return True when the URL host belongs to Naver."""
    host = urlparse(url).hostname or ""
    return any(host == domain.lstrip(".") or host.endswith(domain) for domain in NAVER_DOMAINS)


def _to_mobile_url(url: str) -> str:
    """Normalize a Naver blog URL to the mobile post URL."""
    parsed_url = urlparse(url.strip())
    path_match = BLOG_PATH_RE.match(parsed_url.path)
    if path_match:
        blog_id, post_id = path_match.groups()
        return f"https://m.blog.naver.com/{blog_id}/{post_id}"

    query = parse_qs(parsed_url.query)
    blog_ids = query.get("blogId", [])
    post_ids = query.get("logNo", []) or query.get("postId", [])
    if blog_ids and post_ids:
        return f"https://m.blog.naver.com/{blog_ids[0]}/{post_ids[0]}"

    return url.strip()


def _extract_title(html: str) -> str:
    """Extract a readable title from the HTML."""
    for pattern in [
        r'<meta property="og:title" content="(?P<value>[^"]+)"',
        r"<title>(?P<value>.*?)</title>",
    ]:
        match = re.search(pattern, html, re.IGNORECASE | re.DOTALL)
        if match:
            title = _strip_html(match.group("value"))
            return TITLE_SUFFIX_RE.sub("", title).strip()
    return ""


def _extract_published_at(html: str) -> str:
    """Extract a published date string if available."""
    patterns = [
        r'<meta property="article:published_time" content="(?P<value>[^"]+)"',
        r'<[^>]+class="[^"]*(?:se_publishDate|blog_date|date|publish)[^"]*"[^>]*>(?P<value>.*?)</[^>]+>',
    ]
    for pattern in patterns:
        match = re.search(pattern, html, re.IGNORECASE | re.DOTALL)
        if match:
            return _strip_html(match.group("value"))
    return ""


def _extract_div_block(html: str, start_position: int) -> str:
    """Extract a balanced div block around a matched marker."""
    tag_start = html.rfind("<div", 0, start_position)
    if tag_start < 0:
        tag_start = start_position

    depth = 0
    position = tag_start
    started = False
    html_length = len(html)

    while position < html_length:
        if html[position : position + 4] == "<!--":
            comment_end = html.find("-->", position + 4)
            position = comment_end + 3 if comment_end >= 0 else html_length
            continue

        if html[position : position + 4] == "<div" and (
            position + 4 >= html_length or html[position + 4] in (" ", ">", "\t", "\n", "/")
        ):
            depth += 1
            started = True
        elif html[position : position + 6] == "</div>":
            depth -= 1
            if started and depth == 0:
                return html[tag_start : position + 6]
        position += 1

    return html[tag_start:]


def _extract_content_area(html: str) -> str:
    """Extract the likely Naver mobile blog body container."""
    cleaned_html = SCRIPT_STYLE_RE.sub("", html)
    marker_patterns = [
        r'class="[^"]*\bse-main-container\b[^"]*"',
        r'class="[^"]*\bpost_ct\b[^"]*"',
        r'class="[^"]*\bpostViewArea\b[^"]*"',
        r'class="[^"]*\bpost-view\b[^"]*"',
        r'id="viewTypeSelector"',
    ]
    for pattern in marker_patterns:
        match = re.search(pattern, cleaned_html, re.IGNORECASE)
        if match:
            return _extract_div_block(cleaned_html, match.start())
    return ""


def _extract_text(html_fragment: str) -> str:
    """Convert an HTML fragment into readable text."""
    text = BR_RE.sub("\n", html_fragment)
    text = BLOCK_END_RE.sub("\n", text)
    text = TAG_RE.sub("", text)
    text = unescape(text)

    lines: list[str] = []
    for line in text.split("\n"):
        stripped_line = WHITESPACE_RE.sub(" ", line).strip()
        if stripped_line:
            lines.append(stripped_line)

    result = "\n".join(lines)
    return BLANK_LINES_RE.sub("\n\n", result).strip()


def _extract_body_text(html: str) -> str:
    """Extract readable body text from common Naver blog containers."""
    content_area = _extract_content_area(html)
    if not content_area:
        return ""
    return _extract_text(content_area)


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
    mobile_url = _to_mobile_url(url)
    if not _is_naver_url(mobile_url):
        raise ValueError(f"Not a Naver blog URL: {url}")

    html = _load_html(mobile_url)
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
