"""Tests for Naver blog tools."""

import pytest

from app.core.langgraph.tools.naver_blog_content import (
    _extract_body_text,
    _to_mobile_url,
    fetch_naver_blog_content,
)
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


def test_extract_search_results_prefers_post_cards_and_ignores_promotional_snippets() -> None:
    """Parser should keep the blog-card snippet, not a global promotional block."""
    html = """
    <html>
      <body>
        <div class="api_txt_lines">
          <strong>좋아하는 건 누구나 남기고 싶으니까</strong>
          <p>클립 올리면 매주 쌓이는 Npay 포인트</p>
        </div>
        <div class="result-card">
          <div class="meta">테스터 2026. 1. 14.</div>
          <a class="title_link" href="https://blog.naver.com/tester/224146145933">
            [내돈내산] 서울 중구 - 을지면옥
          </a>
          <div class="desc">정말 맛있었던 평양냉면 후기와 웨이팅 이야기</div>
        </div>
      </body>
    </html>
    """

    results = _extract_search_results(html)

    assert len(results) == 1
    assert results[0]["url"] == "https://blog.naver.com/tester/224146145933"
    assert results[0]["snippet"] == "정말 맛있었던 평양냉면 후기와 웨이팅 이야기"


def test_extract_search_results_prefers_post_url_over_blog_home_url() -> None:
    """Parser should pick the blog post URL instead of the profile URL."""
    html = """
    <html>
      <body>
        <div class="result-card">
          <a class="profile_link" href="https://blog.naver.com/tester">긍정적 우주</a>
          <a class="title_link" href="https://blog.naver.com/tester/224146145933">을지면옥 후기</a>
          <div class="desc">평양냉면과 수육이 좋았던 방문 기록</div>
        </div>
      </body>
    </html>
    """

    results = _extract_search_results(html)

    assert len(results) == 1
    assert results[0]["url"] == "https://blog.naver.com/tester/224146145933"
    assert results[0]["title"] == "을지면옥 후기"


def test_extract_search_results_allows_empty_snippet_for_valid_post_card() -> None:
    """Valid post cards should survive even when no snippet can be extracted."""
    html = """
    <html>
      <body>
        <div class="result-card">
          <div class="meta">테스터 2026. 1. 14.</div>
          <a class="title_link" href="https://blog.naver.com/tester/224146145933">을지면옥 후기</a>
        </div>
      </body>
    </html>
    """

    results = _extract_search_results(html)

    assert len(results) == 1
    assert results[0]["snippet"] == ""


def test_extract_search_results_rejects_blog_home_only_cards() -> None:
    """Cards without a blog post URL should be dropped."""
    html = """
    <html>
      <body>
        <div class="result-card">
          <a class="profile_link" href="https://blog.naver.com/tester">긍정적 우주</a>
          <div class="desc">정말 맛있었던 식당 후기</div>
        </div>
      </body>
    </html>
    """

    assert _extract_search_results(html) == []


def test_to_mobile_url_converts_pc_blog_post_url() -> None:
    """PC blog post URLs should be normalized to mobile post URLs."""
    mobile_url = _to_mobile_url("https://blog.naver.com/jueym/224280701021")

    assert mobile_url == "https://m.blog.naver.com/jueym/224280701021"


def test_extract_body_text_keeps_nested_mobile_container_text() -> None:
    """Nested SmartEditor body divs should not truncate extracted content."""
    html = """
    <html>
      <body>
        <div class="se-main-container">
          <div class="section"><p>첫 문장</p><div><span>둘째 문장</span></div></div>
        </div>
      </body>
    </html>
    """

    assert _extract_body_text(html) == "첫 문장\n둘째 문장"


def test_fetch_naver_blog_content_uses_mobile_extractor(monkeypatch: pytest.MonkeyPatch) -> None:
    """The public tool should fetch the mobile URL and return the existing payload shape."""
    loaded_urls: list[str] = []

    def fake_load_html(url: str) -> str:
        loaded_urls.append(url)
        return """
        <html>
          <head><title>테스트 제목 : 네이버 블로그</title></head>
          <body>
            <span class="se_publishDate">2026. 5. 14. 10:20</span>
            <div class="se-main-container"><p>본문 미리보기</p></div>
          </body>
        </html>
        """

    monkeypatch.setattr("app.core.langgraph.tools.naver_blog_content._load_html", fake_load_html)

    result = fetch_naver_blog_content.invoke({"url": "https://blog.naver.com/tester/123"})

    assert loaded_urls == ["https://m.blog.naver.com/tester/123"]
    assert result["title"] == "테스트 제목"
    assert result["published_at"] == "2026. 5. 14. 10:20"
    assert result["raw_text"] == "본문 미리보기"
    assert result["fetch_status"] == "success"
