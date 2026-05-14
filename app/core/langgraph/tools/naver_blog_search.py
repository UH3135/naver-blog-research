"""Naver blog search tool."""

import json
import re
from dataclasses import dataclass
from html import unescape
from typing import Any
from urllib.parse import quote_plus
from urllib.request import Request, urlopen

from langchain_core.tools import tool
from lxml import html as lxml_html
from lxml.html import HtmlElement
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
PROMOTIONAL_KEYWORDS = (
    "npay",
    "포인트",
    "쿠폰",
    "적립",
    "첫 구매",
    "스토어",
    "브라우저",
    "웨일",
    "클립",
)
DATE_PATTERNS = (
    re.compile(r"\d{4}\.\s*\d{1,2}\.\s*\d{1,2}\."),
    re.compile(r"\d{1,2}:\d{2}"),
    re.compile(r"\d+일 전"),
)
POST_URL_PATTERN = re.compile(r"^https?://blog\.naver\.com/[^/]+/\d+(?:\?.*)?$", re.IGNORECASE)
HOME_URL_PATTERN = re.compile(r"^https?://blog\.naver\.com/[^/]+/?(?:\?.*)?$", re.IGNORECASE)
CARD_TAGS = {"div", "li", "section", "article"}


@dataclass
class SearchCardCandidate:
    """Candidate card element for one blog result."""

    element: HtmlElement
    source_selector: str


@dataclass
class SearchFieldCandidate:
    """Field candidate with score metadata."""

    value: str
    score: int
    source_selector: str


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
        "published_at": _strip_html(str(item.get("postdate", item.get("published_at", "")))),
    }


def _normalize_text(value: str) -> str:
    """Normalize whitespace in extracted text."""
    return re.sub(r"\s+", " ", value).strip()


def _normalize_url(url: str) -> str:
    """Normalize an extracted URL."""
    return unescape(url).strip()


def _element_text(element: HtmlElement) -> str:
    """Get normalized visible text from an HTML element."""
    return _normalize_text(" ".join(element.itertext()))


def _is_blog_post_url(url: str) -> bool:
    """Return True when URL points to a concrete blog post."""
    return bool(POST_URL_PATTERN.match(url))


def _is_blog_home_url(url: str) -> bool:
    """Return True when URL points to a blog home/profile page."""
    return bool(HOME_URL_PATTERN.match(url)) and not _is_blog_post_url(url)


def _is_promotional_text(text: str) -> bool:
    """Detect promotional copy that should not be used as snippets."""
    normalized_text = text.lower()
    keyword_matches = sum(1 for keyword in PROMOTIONAL_KEYWORDS if keyword in normalized_text)
    return keyword_matches >= 2 or ("혜택" in text and "적립" in text)


def _contains_query_term(text: str) -> bool:
    """Detect whether a text looks query-relevant for food search results."""
    return any(term in text for term in ("후기", "맛집", "을지면옥", "중구", "평양냉면", "식당", "웨이팅"))


def _looks_like_date(text: str) -> bool:
    """Detect common published-at patterns."""
    return any(pattern.search(text) for pattern in DATE_PATTERNS)


def _is_breadcrumb_text(text: str) -> bool:
    """Detect breadcrumb-style source labels."""
    return "blog.naver.com" in text or "›" in text


def _is_keep_text(text: str) -> bool:
    """Detect keep shortcut labels."""
    return "Keep" in text


def _find_card_container(anchor: HtmlElement) -> HtmlElement:
    """Find the closest reasonable card container for a blog post anchor."""
    current = anchor
    while current is not None:
        if current.tag in CARD_TAGS:
            text_content = _element_text(current)
            post_links = current.xpath('.//a[starts-with(@href, "http")]')
            post_link_count = sum(1 for link in post_links if _is_blog_post_url(_normalize_url(link.get("href", ""))))
            if len(text_content) >= 12 and post_link_count >= 1:
                return current
        current = current.getparent()
    parent = anchor.getparent()
    return parent if parent is not None else anchor


def _collect_card_candidates(html: str) -> list[SearchCardCandidate]:
    """Collect candidate result cards from blog post anchors."""
    document = lxml_html.fromstring(html)
    candidates: list[SearchCardCandidate] = []
    seen_elements: set[int] = set()
    for anchor in document.xpath('.//a[starts-with(@href, "http")]'):
        url = _normalize_url(anchor.get("href", ""))
        if not _is_blog_post_url(url):
            continue
        container = _find_card_container(anchor)
        container_id = id(container)
        if container_id in seen_elements:
            continue
        seen_elements.add(container_id)
        candidates.append(SearchCardCandidate(element=container, source_selector="post_anchor_container"))
    return candidates


def _iter_link_elements(card: HtmlElement) -> list[HtmlElement]:
    """Return link elements from a card, including the card itself when needed."""
    if card.tag == "a":
        return [card]
    return list(card.xpath('.//a[starts-with(@href, "http")]'))


def _score_url_candidate(anchor: HtmlElement) -> SearchFieldCandidate:
    """Score a URL candidate from a card anchor."""
    url = _normalize_url(anchor.get("href", ""))
    score = 0
    if _is_blog_post_url(url):
        score += 8
    elif _is_blog_home_url(url):
        score -= 4
    text = _element_text(anchor)
    if text:
        score += 1
    return SearchFieldCandidate(value=url, score=score, source_selector="anchor.href")


def _score_title_candidate(anchor: HtmlElement) -> SearchFieldCandidate:
    """Score a title candidate from a card anchor."""
    text = _element_text(anchor)
    url = _normalize_url(anchor.get("href", ""))
    score = 0
    if _is_blog_post_url(url):
        score += 5
    if 4 <= len(text) <= 120:
        score += 3
    if _contains_query_term(text):
        score += 2
    if len(text) > 160:
        score -= 4
    if _is_breadcrumb_text(text):
        score -= 6
    if _is_keep_text(text):
        score -= 8
    if _is_promotional_text(text):
        score -= 8
    if _is_blog_home_url(url):
        score -= 5
    return SearchFieldCandidate(value=text, score=score, source_selector="anchor.text")


def _score_snippet_candidate(element: HtmlElement, title: str) -> SearchFieldCandidate:
    """Score a snippet candidate from a card descendant element."""
    text = _element_text(element)
    score = 0
    if 20 <= len(text) <= 300:
        score += 3
    if _contains_query_term(text):
        score += 2
    class_name = str(element.get("class", ""))
    if any(token in class_name.lower() for token in ("desc", "dsc", "body", "ellipsis", "text")):
        score += 2
    if element.tag in {"p", "div", "span", "a"}:
        score += 1
    if title and text == title:
        score -= 4
    if title and title in text:
        score -= 1
    if _is_breadcrumb_text(text):
        score -= 6
    if _is_keep_text(text):
        score -= 8
    if _looks_like_date(text):
        score -= 2
    if _is_promotional_text(text):
        score -= 10
    return SearchFieldCandidate(value=text, score=score, source_selector=f"{element.tag}.{class_name}".strip("."))


def _extract_best_url(card: HtmlElement) -> str:
    """Extract the best URL candidate from a card."""
    url_candidates = [_score_url_candidate(anchor) for anchor in _iter_link_elements(card)]
    if not url_candidates:
        return ""
    best_candidate = max(url_candidates, key=lambda candidate: candidate.score)
    return best_candidate.value if best_candidate.score > 0 and _is_blog_post_url(best_candidate.value) else ""


def _extract_best_title(card: HtmlElement) -> str:
    """Extract the best title candidate from a card."""
    title_candidates = [_score_title_candidate(anchor) for anchor in _iter_link_elements(card)]
    valid_candidates = [candidate for candidate in title_candidates if candidate.value]
    if not valid_candidates:
        return ""
    best_candidate = max(valid_candidates, key=lambda candidate: candidate.score)
    return best_candidate.value if best_candidate.score >= 5 else ""


def _extract_best_snippet(card: HtmlElement, title: str) -> str:
    """Extract the best snippet candidate from a card."""
    snippet_candidates: list[SearchFieldCandidate] = []
    for element in card.xpath(".//*"):
        text = _element_text(element)
        if not text or len(text) < 20:
            continue
        snippet_candidates.append(_score_snippet_candidate(element, title))
    valid_candidates = [candidate for candidate in snippet_candidates if candidate.score > 0]
    if not valid_candidates:
        return ""
    best_candidate = max(valid_candidates, key=lambda candidate: candidate.score)
    return "" if _is_promotional_text(best_candidate.value) else best_candidate.value


def _extract_published_at(card: HtmlElement) -> str:
    """Extract published-at text from the card."""
    for element in card.xpath(".//*"):
        text = _element_text(element)
        if text and len(text) <= 40 and _looks_like_date(text):
            return text
    return ""


def _score_result_payload(result: dict[str, str]) -> int:
    """Score a parsed card payload so the best card wins per URL."""
    score = 0
    if result["title"]:
        score += 4
    if not _is_breadcrumb_text(result["title"]):
        score += 3
    else:
        score -= 5
    if result["snippet"]:
        score += 2
    if result["snippet"] and result["snippet"] != result["title"]:
        score += 2
    if _is_breadcrumb_text(result["snippet"]) or _is_keep_text(result["snippet"]):
        score -= 5
    if result["published_at"]:
        score += 1
    return score


def _extract_search_results(html: str) -> list[dict[str, str]]:
    """Extract blog candidates from HTML."""
    best_results_by_url: dict[str, tuple[int, dict[str, str]]] = {}
    for card_candidate in _collect_card_candidates(html):
        url = _extract_best_url(card_candidate.element)
        title = _extract_best_title(card_candidate.element)
        if not url or not title:
            continue
        result = {
            "title": title,
            "url": url,
            "snippet": _extract_best_snippet(card_candidate.element, title),
            "published_at": _extract_published_at(card_candidate.element),
        }
        result_score = _score_result_payload(result)
        existing_result = best_results_by_url.get(url)
        if existing_result is None or result_score > existing_result[0]:
            best_results_by_url[url] = (result_score, result)
    results = [
        result
        for _, result in sorted(
            best_results_by_url.values(),
            key=lambda item: item[0],
            reverse=True,
        )
    ]
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
