# Naver Blog Mobile Content Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the production Naver blog content tool use the successful mobile-page body extraction flow.

**Architecture:** Keep `fetch_naver_blog_content` as the single public production tool. Add focused private helpers in `app/core/langgraph/tools/naver_blog_content.py` for URL normalization, Naver URL validation, balanced container extraction, title/date extraction, and HTML-to-text normalization.

**Tech Stack:** Python 3.13, LangChain tool decorator, tenacity retry, structlog wrapper, pytest.

---

### Task 1: Add Focused Content Parser Tests

**Files:**
- Modify: `tests/core/langgraph/tools/test_naver_blog_tools.py`

- [ ] **Step 1: Import content parser helpers and public tool**

```python
from app.core.langgraph.tools.naver_blog_content import (
    _extract_body_text,
    _to_mobile_url,
    fetch_naver_blog_content,
)
```

- [ ] **Step 2: Add URL normalization and nested body tests**

```python
def test_to_mobile_url_converts_pc_blog_post_url() -> None:
    mobile_url = _to_mobile_url("https://blog.naver.com/jueym/224280701021")

    assert mobile_url == "https://m.blog.naver.com/jueym/224280701021"


def test_extract_body_text_keeps_nested_mobile_container_text() -> None:
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
```

- [ ] **Step 3: Add public tool test with monkeypatched loader**

```python
def test_fetch_naver_blog_content_uses_mobile_extractor(monkeypatch: pytest.MonkeyPatch) -> None:
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
```

- [ ] **Step 4: Run failing tests**

Run: `uv run pytest tests/core/langgraph/tools/test_naver_blog_tools.py -q`

Expected before implementation: import or assertion failures for missing mobile parser behavior.

### Task 2: Implement Mobile Content Extraction

**Files:**
- Modify: `app/core/langgraph/tools/naver_blog_content.py`

- [ ] **Step 1: Add mobile parser constants and helpers**

Add URL regexes, Naver domain validation, mobile URL normalization, balanced div extraction, content-area selection, and text normalization helpers directly in `naver_blog_content.py`.

- [ ] **Step 2: Replace iframe fetch path in `fetch_naver_blog_content`**

Load only the normalized mobile URL, extract title/date/body from that HTML, and preserve the existing result shape.

- [ ] **Step 3: Run focused tests**

Run: `uv run pytest tests/core/langgraph/tools/test_naver_blog_tools.py -q`

Expected after implementation: all tests pass.

### Task 3: Verify Workflow Compatibility

**Files:**
- Test only: `tests/core/langgraph/test_naver_blog_graph.py`
- Test only: `tests/api/v1/test_naver_blog.py`

- [ ] **Step 1: Run graph and API tests**

Run: `uv run pytest tests/core/langgraph/test_naver_blog_graph.py tests/api/v1/test_naver_blog.py -q`

Expected: all tests pass with unchanged response schema.

- [ ] **Step 2: Run live smoke check for the four sample URLs**

Run the production tool against the four provided Naver blog URLs and compare `fetch_status` plus `raw_text` length.

Expected: all four return `fetch_status="success"` and body lengths close to the previous `naver_read_mobile.py` results.
