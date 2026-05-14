# Naver Blog Selector Parser Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the brittle global regex-based Naver blog search parser with a resilient card-based parser that avoids promotional snippet contamination and prefers blog post URLs over blog home URLs.

**Architecture:** Keep `search_naver_blog()` as the public tool entry point, but route HTML parsing through a new card-based pipeline inside `app/core/langgraph/tools/naver_blog_search.py`. Parse result cards first, collect field candidates per card, score candidates with deterministic rules, and post-filter accepted results so bad snippets become empty strings instead of polluting otherwise valid blog post results.

**Tech Stack:** Python 3.13, stdlib `html.parser`-adjacent regex/string helpers already in repo, tenacity, pytest

---

### Task 1: Add failing regression tests for promotional snippet contamination

**Files:**
- Modify: `tests/core/langgraph/tools/test_naver_blog_tools.py`
- Test: `tests/core/langgraph/tools/test_naver_blog_tools.py`

- [ ] **Step 1: Write the failing test**

```python
def test_extract_search_results_prefers_post_cards_and_ignores_promotional_snippets() -> None:
    html = """
    <html>
      <body>
        <div class="api_txt_lines">
          <strong>좋아하는 건 누구나 남기고 싶으니까</strong>
          <p>클립 올리면 매주 쌓이는 Npay 포인트</p>
        </div>
        <div class="result-card">
          <div class="meta">테스터 2026. 1. 14.</div>
          <a class="title_link" href="https://blog.naver.com/tester/224146145933">[내돈내산] 서울 중구 - 을지면옥</a>
          <div class="desc">정말 맛있었던 평양냉면 후기와 웨이팅 이야기</div>
        </div>
      </body>
    </html>
    """

    results = _extract_search_results(html)

    assert len(results) == 1
    assert results[0]["url"] == "https://blog.naver.com/tester/224146145933"
    assert results[0]["snippet"] == "정말 맛있었던 평양냉면 후기와 웨이팅 이야기"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/core/langgraph/tools/test_naver_blog_tools.py::test_extract_search_results_prefers_post_cards_and_ignores_promotional_snippets -v`
Expected: FAIL because the current parser either returns the promotional text as `snippet` or fails to isolate the correct card.

- [ ] **Step 3: Add a second failing test for URL preference**

```python
def test_extract_search_results_prefers_post_url_over_blog_home_url() -> None:
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
```

- [ ] **Step 4: Run both tests to verify they fail**

Run: `uv run pytest tests/core/langgraph/tools/test_naver_blog_tools.py -k 'promotional or prefers_post_url' -v`
Expected: FAIL with the current global list-and-index parsing behavior.

- [ ] **Step 5: Commit**

```bash
git add tests/core/langgraph/tools/test_naver_blog_tools.py
git commit -m "test: cover naver blog selector parser regressions"
```

### Task 2: Implement card-based parsing primitives

**Files:**
- Modify: `app/core/langgraph/tools/naver_blog_search.py`
- Test: `tests/core/langgraph/tools/test_naver_blog_tools.py`

- [ ] **Step 1: Add parser data structures and helper predicates**

```python
@dataclass
class SearchFieldCandidate:
    value: str
    score: int = 0


@dataclass
class SearchCardCandidate:
    html_fragment: str
    text_content: str


def _is_blog_post_url(url: str) -> bool:
    return bool(re.match(r"^https?://blog\.naver\.com/[^/]+/\d+", url))


def _is_blog_home_url(url: str) -> bool:
    return bool(re.match(r"^https?://blog\.naver\.com/[^/]+/?$", url))
```

- [ ] **Step 2: Add promotional-text detection and text normalization helpers**

```python
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


def _is_promotional_text(text: str) -> bool:
    normalized_text = text.lower()
    match_count = sum(1 for keyword in PROMOTIONAL_KEYWORDS if keyword in normalized_text)
    return match_count >= 2 or ("혜택" in text and "적립" in text)
```

- [ ] **Step 3: Implement card collection before field extraction**

```python
def _collect_card_candidates(html: str) -> list[SearchCardCandidate]:
    card_pattern = re.compile(
        r"<(?P<tag>div|li|section|article)(?P<attrs>[^>]*)>(?P<body>.*?)</(?P=tag)>",
        re.IGNORECASE | re.DOTALL,
    )
    candidates = []
    for match in card_pattern.finditer(html):
        fragment = match.group(0)
        if not re.search(r'https?://blog\.naver\.com/[^/]+/\d+', fragment):
            continue
        text_content = _strip_html(fragment)
        if len(text_content) < 20:
            continue
        candidates.append(SearchCardCandidate(html_fragment=fragment, text_content=text_content))
    return candidates
```

- [ ] **Step 4: Implement field candidate extraction and deterministic scoring**

```python
def _extract_best_url(card_html: str) -> str:
    ...


def _extract_best_title(card_html: str) -> str:
    ...


def _extract_best_snippet(card_html: str, title: str) -> str:
    ...
```

Use these rules:
- Prefer `blog.naver.com/<blog>/<post_id>` over blog home URLs
- Ignore empty anchors and profile anchors for titles
- Reject promotional snippet candidates
- Fall back to `""` for `snippet` when all candidates are weak

- [ ] **Step 5: Replace `_extract_search_results()` with card-based assembly**

```python
def _extract_search_results(html: str) -> list[dict[str, str]]:
    results: list[dict[str, str]] = []
    seen_urls: set[str] = set()
    for card in _collect_card_candidates(html):
        url = _extract_best_url(card.html_fragment)
        title = _extract_best_title(card.html_fragment)
        if not url or not title or url in seen_urls:
            continue
        seen_urls.add(url)
        results.append(
            {
                "title": title,
                "url": url,
                "snippet": _extract_best_snippet(card.html_fragment, title),
                "blogger_name": _extract_blogger_name(card.html_fragment),
                "published_at": _extract_published_at(card.html_fragment),
            }
        )
    return results
```

- [ ] **Step 6: Run the focused test file**

Run: `uv run pytest tests/core/langgraph/tools/test_naver_blog_tools.py -v`
Expected: PASS for the new regression tests and the existing normalization test.

- [ ] **Step 7: Commit**

```bash
git add app/core/langgraph/tools/naver_blog_search.py tests/core/langgraph/tools/test_naver_blog_tools.py
git commit -m "feat: add resilient naver blog selector parser"
```

### Task 3: Add parser edge-case coverage and verify search-preview integration assumptions

**Files:**
- Modify: `tests/core/langgraph/tools/test_naver_blog_tools.py`
- Test: `tests/core/langgraph/tools/test_naver_blog_tools.py`

- [ ] **Step 1: Add a test that keeps a card with an empty snippet**

```python
def test_extract_search_results_allows_empty_snippet_for_valid_post_card() -> None:
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
```

- [ ] **Step 2: Add a test that rejects blog home-only cards**

```python
def test_extract_search_results_rejects_blog_home_only_cards() -> None:
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
```

- [ ] **Step 3: Run the test file again**

Run: `uv run pytest tests/core/langgraph/tools/test_naver_blog_tools.py -v`
Expected: PASS with coverage for empty-snippet and home-only rejection behavior.

- [ ] **Step 4: Commit**

```bash
git add tests/core/langgraph/tools/test_naver_blog_tools.py
git commit -m "test: add naver blog parser edge case coverage"
```

### Task 4: Verify the implementation against project conventions

**Files:**
- Modify: `app/core/langgraph/tools/naver_blog_search.py` if needed
- Test: `tests/core/langgraph/tools/test_naver_blog_tools.py`

- [ ] **Step 1: Run lint-style targeted verification**

Run: `uv run pytest tests/core/langgraph/tools/test_naver_blog_tools.py tests/core/langgraph/test_naver_blog_graph.py -v`
Expected: PASS with no regressions in graph-level tests that assume `snippet` remains a string field.

- [ ] **Step 2: Inspect the search tool for project-rule compliance**

Check these points in `app/core/langgraph/tools/naver_blog_search.py`:
- imports stay at the top
- no in-function imports
- no f-strings in structlog events
- helper names and return types stay typed
- `snippet` fallback remains `""` not `None`

- [ ] **Step 3: Commit final cleanup if any code changes were needed**

```bash
git add app/core/langgraph/tools/naver_blog_search.py tests/core/langgraph/tools/test_naver_blog_tools.py
git commit -m "chore: finalize naver blog parser verification"
```
