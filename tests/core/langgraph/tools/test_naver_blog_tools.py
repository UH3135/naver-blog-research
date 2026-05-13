"""Tests for Naver blog tools."""

from app.core.langgraph.tools.naver_blog_search import (
    _extract_search_results,
    _normalize_search_item,
)


def test_normalize_search_item_maps_missing_fields() -> None:
    """Normalization should fill missing optional fields."""
    normalized_item = _normalize_search_item({"title": "테스트", "link": "https://example.com"})

    assert normalized_item["title"] == "테스트"
    assert normalized_item["url"] == "https://example.com"
    assert normalized_item["snippet"] == ""
    assert normalized_item["blogger_name"] == ""


def test_extract_search_results_returns_blog_candidates() -> None:
    """HTML parsing should return blog candidate metadata."""
    html = """
    <html>
      <body>
        <a class="title_link" href="https://blog.naver.com/test/123">맛집 후기</a>
        <div class="user_info">테스터</div>
        <div class="dsc_area">정말 맛있었던 식당 후기</div>
      </body>
    </html>
    """

    results = _extract_search_results(html)

    assert len(results) == 1
    assert results[0]["title"] == "맛집 후기"
    assert results[0]["url"] == "https://blog.naver.com/test/123"
