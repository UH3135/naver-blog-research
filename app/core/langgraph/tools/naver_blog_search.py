"""Naver blog search tool."""

import json
import re
from html import unescape
from typing import Any
from urllib.parse import quote_plus
from urllib.request import Request, urlopen

from langchain_core.tools import tool
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
)

from app.core.logging import logger

NAVER_SEARCH_URL = "https://search.naver.com/search.naver?where=view&sm=tab_jum&query="
REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
}


def _strip_html(value: str) -> str:
    """Remove HTML tags and entities from a string."""
    tag_stripped = re.sub(r"<[^>]+>", "", value)
    whitespace_normalized = re.sub(r"\s+", " ", tag_stripped)
    return unescape(whitespace_normalized).strip()


def _normalize_search_item(item: dict[str, Any]) -> dict[str, str]:
    """Normalize a raw search result item."""
    return {
        "title": _strip_html(str(item.get("title", ""))),
        "url": str(item.get("link", item.get("url", ""))).strip(),
        "snippet": _strip_html(str(item.get("description", item.get("snippet", "")))),
        "blogger_name": _strip_html(str(item.get("blogger_name", item.get("bloggerName", "")))),
        "published_at": _strip_html(str(item.get("postdate", item.get("published_at", "")))),
    }


def _extract_search_results(html: str) -> list[dict[str, str]]:
    """Extract blog candidates from HTML."""
    results: list[dict[str, str]] = []
    anchor_pattern = re.compile(
        r'<a[^>]+href="(?P<url>https?://blog\.naver\.com/[^"]+)"[^>]*>(?P<title>.*?)</a>',
        re.IGNORECASE | re.DOTALL,
    )
    snippet_pattern = re.compile(r'<div[^>]+class="[^"]*(?:dsc_area|total_dsc|api_txt_lines)[^"]*"[^>]*>(.*?)</div>', re.DOTALL)
    blogger_pattern = re.compile(r'<div[^>]+class="[^"]*(?:user_info|name|sub)[^"]*"[^>]*>(.*?)</div>', re.DOTALL)

    snippets = [_strip_html(match) for match in snippet_pattern.findall(html)]
    bloggers = [_strip_html(match) for match in blogger_pattern.findall(html)]

    for index, match in enumerate(anchor_pattern.finditer(html)):
        results.append(
            {
                "title": _strip_html(match.group("title")),
                "url": match.group("url").strip(),
                "snippet": snippets[index] if index < len(snippets) else "",
                "blogger_name": bloggers[index] if index < len(bloggers) else "",
                "published_at": "",
            }
        )
    return results


def _extract_api_results(html: str) -> list[dict[str, str]]:
    """Extract result items from Naver's embedded JSON when available."""
    json_pattern = re.compile(r"__NEXT_DATA__\"[^>]*>(?P<payload>\{.*?\})</script>", re.DOTALL)
    match = json_pattern.search(html)
    if not match:
        return []

    try:
        payload = json.loads(match.group("payload"))
    except json.JSONDecodeError:
        return []

    results: list[dict[str, str]] = []
    for candidate in _walk_for_blog_candidates(payload):
        normalized_item = _normalize_search_item(candidate)
        if normalized_item["url"]:
            results.append(normalized_item)
    return results


def _walk_for_blog_candidates(payload: Any) -> list[dict[str, Any]]:
    """Recursively find candidate dictionaries that look like blog results."""
    candidates: list[dict[str, Any]] = []
    if isinstance(payload, dict):
        url = str(payload.get("link", payload.get("url", "")))
        if "blog.naver.com" in url:
            candidates.append(payload)
        for value in payload.values():
            candidates.extend(_walk_for_blog_candidates(value))
    elif isinstance(payload, list):
        for item in payload:
            candidates.extend(_walk_for_blog_candidates(item))
    return candidates


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=4), reraise=True)
def _load_search_html(query: str) -> str:
    """Load the Naver search HTML."""
    request = Request(f"{NAVER_SEARCH_URL}{quote_plus(query)}", headers=REQUEST_HEADERS)
    with urlopen(request, timeout=10) as response:  # noqa: S310
        return response.read().decode("utf-8", errors="ignore")


@tool
def search_naver_blog(query: str, max_results: int = 3) -> list[dict[str, str]]:
    """Search Naver blog results."""
    logger.info("naver_blog_search_started", query=query, max_results=max_results)
    html = _load_search_html(query)
    results = _extract_api_results(html)
    if not results:
        results = _extract_search_results(html)

    normalized_results = []
    seen_urls: set[str] = set()
    for result in results:
        normalized_result = _normalize_search_item(result)
        if not normalized_result["url"] or normalized_result["url"] in seen_urls:
            continue
        seen_urls.add(normalized_result["url"])
        normalized_results.append(normalized_result)
        if len(normalized_results) >= max_results:
            break

    logger.info("naver_blog_search_completed", query=query, result_count=len(normalized_results))
    return normalized_results
