# Naver Blog Search Preview Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 식당명과 지역을 입력받아 네이버 블로그 검색과 본문 수집 미리보기를 수행하는 LangGraph 기반 API를 구현한다.

**Architecture:** FastAPI 전용 라우트가 Pydantic 요청 모델을 검증한 뒤, 별도 LangGraph preview workflow를 실행한다. 워크플로우는 검색 질의 정규화, 네이버 검색 도구 호출, fallback 재시도, 본문 수집 도구 호출, 응답 조립을 수행하고 Langfuse 추적 메타데이터를 남긴다.

**Tech Stack:** FastAPI, Pydantic v2, LangGraph, Langfuse, structlog, tenacity, pytest

---

## File Structure

- Create: `app/api/v1/naver_blog.py`
  - 네이버 블로그 search preview 전용 라우트
- Create: `app/schemas/naver_blog.py`
  - request/response/item/error/state 관련 Pydantic 모델
- Create: `app/core/langgraph/naver_blog_graph.py`
  - preview workflow 오케스트레이션
- Create: `app/core/langgraph/tools/naver_blog_search.py`
  - 네이버 블로그 검색 도구
- Create: `app/core/langgraph/tools/naver_blog_content.py`
  - 네이버 블로그 본문 수집 도구
- Modify: `app/core/langgraph/tools/__init__.py`
  - 기존 tools export 유지 + 새 도구 export 추가
- Modify: `app/api/v1/api.py`
  - 신규 router 등록
- Modify: `app/schemas/__init__.py`
  - 신규 schema export
- Modify: `app/core/config.py`
  - `naver_blog_preview` rate limit 기본값 추가
- Create: `tests/api/v1/test_naver_blog.py`
  - API validation/response 테스트
- Create: `tests/core/langgraph/test_naver_blog_graph.py`
  - workflow 상태 전이 테스트
- Create: `tests/core/langgraph/tools/test_naver_blog_tools.py`
  - search/fetch 도구 contract 테스트

### Task 1: Request/Response Schema 추가

**Files:**
- Create: `app/schemas/naver_blog.py`
- Modify: `app/schemas/__init__.py`
- Test: `tests/api/v1/test_naver_blog.py`

- [ ] **Step 1: Write the failing schema validation test**

```python
from pydantic import ValidationError

from app.schemas.naver_blog import NaverBlogPreviewRequest


def test_naver_blog_preview_request_rejects_blank_region() -> None:
    try:
        NaverBlogPreviewRequest(restaurant_name="을지면옥", region="   ")
    except ValidationError as exc:
        assert "region" in str(exc)
    else:
        raise AssertionError("ValidationError was not raised")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/api/v1/test_naver_blog.py::test_naver_blog_preview_request_rejects_blank_region -v`
Expected: FAIL with `ModuleNotFoundError` or missing schema failure

- [ ] **Step 3: Write minimal schema implementation**

```python
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator


class NaverBlogPreviewRequest(BaseModel):
    restaurant_name: str = Field(..., min_length=1, max_length=200)
    region: str = Field(..., min_length=1, max_length=200)
    max_results: int = Field(default=3, ge=1, le=5)

    @field_validator("restaurant_name", "region")
    @classmethod
    def validate_non_blank(cls, value: str) -> str:
        stripped_value = value.strip()
        if not stripped_value:
            raise ValueError("value must not be blank")
        return stripped_value


class NaverBlogPreviewError(BaseModel):
    code: str
    message: str
    target: Optional[str] = None


class NaverBlogPreviewItem(BaseModel):
    title: str
    url: str
    snippet: str = ""
    blogger_name: str = ""
    published_at: Optional[str] = None
    excerpt: str = ""
    raw_text_available: bool = False
    fetch_status: Literal["pending", "success", "failed"] = "pending"


class NaverBlogPreviewResponse(BaseModel):
    status: Literal["success", "partial_success", "failed"]
    query: dict[str, str]
    search_query: str
    items: list[NaverBlogPreviewItem]
    errors: list[NaverBlogPreviewError] = Field(default_factory=list)
    metadata: dict[str, int | str | bool]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/api/v1/test_naver_blog.py::test_naver_blog_preview_request_rejects_blank_region -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/schemas/naver_blog.py app/schemas/__init__.py tests/api/v1/test_naver_blog.py
git commit -m "feat: 네이버 블로그 미리보기 스키마 추가"
```

### Task 2: Search/Fetch Tool Contract 추가

**Files:**
- Create: `app/core/langgraph/tools/naver_blog_search.py`
- Create: `app/core/langgraph/tools/naver_blog_content.py`
- Modify: `app/core/langgraph/tools/__init__.py`
- Test: `tests/core/langgraph/tools/test_naver_blog_tools.py`

- [ ] **Step 1: Write the failing tool normalization test**

```python
from app.core.langgraph.tools.naver_blog_search import _normalize_search_item


def test_normalize_search_item_maps_missing_fields() -> None:
    normalized_item = _normalize_search_item({"title": "테스트", "link": "https://example.com"})

    assert normalized_item["title"] == "테스트"
    assert normalized_item["url"] == "https://example.com"
    assert normalized_item["snippet"] == ""
    assert normalized_item["blogger_name"] == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/core/langgraph/tools/test_naver_blog_tools.py::test_normalize_search_item_maps_missing_fields -v`
Expected: FAIL because helper does not exist

- [ ] **Step 3: Write minimal tool implementation**

```python
def _normalize_search_item(item: dict) -> dict[str, str]:
    return {
        "title": item.get("title", "").strip(),
        "url": item.get("link", "").strip(),
        "snippet": item.get("description", "").strip(),
        "blogger_name": item.get("blogger_name", "").strip(),
        "published_at": item.get("postdate", "").strip(),
    }
```

```python
@tool
def search_naver_blog(query: str, max_results: int = 3) -> list[dict[str, str]]:
    ...


@tool
def fetch_naver_blog_content(url: str) -> dict[str, str | bool]:
    ...
```

- [ ] **Step 4: Run focused tool tests**

Run: `uv run pytest tests/core/langgraph/tools/test_naver_blog_tools.py -v`
Expected: PASS for normalization tests, remaining tests fail until fetch/search are completed

- [ ] **Step 5: Commit**

```bash
git add app/core/langgraph/tools/naver_blog_search.py app/core/langgraph/tools/naver_blog_content.py app/core/langgraph/tools/__init__.py tests/core/langgraph/tools/test_naver_blog_tools.py
git commit -m "feat: 네이버 블로그 검색 도구 추가"
```

### Task 3: Preview Workflow 추가

**Files:**
- Create: `app/core/langgraph/naver_blog_graph.py`
- Create: `tests/core/langgraph/test_naver_blog_graph.py`
- Modify: `app/schemas/naver_blog.py`

- [ ] **Step 1: Write the failing fallback workflow test**

```python
import pytest

from app.core.langgraph.naver_blog_graph import NaverBlogPreviewGraph
from app.schemas.naver_blog import NaverBlogPreviewRequest


@pytest.mark.asyncio
async def test_preview_graph_retries_once_when_search_results_are_empty() -> None:
    graph = NaverBlogPreviewGraph()
    graph.search_tool = lambda query, max_results: []

    response = await graph.run(NaverBlogPreviewRequest(restaurant_name="을지면옥", region="중구"))

    assert response.status == "failed"
    assert response.metadata["search_retry_count"] == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/core/langgraph/test_naver_blog_graph.py::test_preview_graph_retries_once_when_search_results_are_empty -v`
Expected: FAIL because graph class does not exist

- [ ] **Step 3: Write minimal workflow implementation**

```python
class NaverBlogPreviewGraph:
    def __init__(self) -> None:
        self.search_tool = search_naver_blog
        self.fetch_tool = fetch_naver_blog_content

    async def run(self, request: NaverBlogPreviewRequest) -> NaverBlogPreviewResponse:
        search_query = f"{request.region} {request.restaurant_name} 블로그"
        search_results = self.search_tool.invoke({"query": search_query, "max_results": request.max_results})
        retry_count = 0
        if not search_results:
            retry_count = 1
            search_query = f"{request.restaurant_name} {request.region} 후기"
            search_results = self.search_tool.invoke({"query": search_query, "max_results": request.max_results})
        ...
```

- [ ] **Step 4: Run graph tests**

Run: `uv run pytest tests/core/langgraph/test_naver_blog_graph.py -v`
Expected: PASS for retry/status tests

- [ ] **Step 5: Commit**

```bash
git add app/core/langgraph/naver_blog_graph.py app/schemas/naver_blog.py tests/core/langgraph/test_naver_blog_graph.py
git commit -m "feat: 네이버 블로그 preview 그래프 추가"
```

### Task 4: API Route 연결

**Files:**
- Create: `app/api/v1/naver_blog.py`
- Modify: `app/api/v1/api.py`
- Modify: `app/core/config.py`
- Test: `tests/api/v1/test_naver_blog.py`

- [ ] **Step 1: Write the failing API contract test**

```python
from fastapi.testclient import TestClient

from app.main import app


def test_naver_blog_preview_rejects_blank_restaurant_name() -> None:
    client = TestClient(app)

    response = client.post(
        "/api/v1/naver-blog/search-preview",
        json={"restaurant_name": " ", "region": "중구"},
    )

    assert response.status_code == 422
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/api/v1/test_naver_blog.py::test_naver_blog_preview_rejects_blank_restaurant_name -v`
Expected: FAIL with 404 or missing route

- [ ] **Step 3: Write minimal route implementation**

```python
router = APIRouter()
preview_graph = NaverBlogPreviewGraph()


@router.post("/search-preview", response_model=NaverBlogPreviewResponse)
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["naver_blog_preview"][0])
async def search_preview(
    request: Request,
    preview_request: NaverBlogPreviewRequest,
    session: Session = Depends(get_current_session),
) -> NaverBlogPreviewResponse:
    logger.info(
        "naver_blog_preview_request_received",
        session_id=session.id,
        restaurant_name=preview_request.restaurant_name,
        region=preview_request.region,
    )
    return await preview_graph.run(preview_request)
```

- [ ] **Step 4: Run API tests**

Run: `uv run pytest tests/api/v1/test_naver_blog.py -v`
Expected: PASS for validation and success-path response tests

- [ ] **Step 5: Commit**

```bash
git add app/api/v1/naver_blog.py app/api/v1/api.py app/core/config.py tests/api/v1/test_naver_blog.py
git commit -m "feat: 네이버 블로그 preview API 추가"
```

### Task 5: Langfuse Metadata와 통합 검증

**Files:**
- Modify: `app/core/langgraph/naver_blog_graph.py`
- Modify: `tests/core/langgraph/test_naver_blog_graph.py`
- Modify: `tests/api/v1/test_naver_blog.py`

- [ ] **Step 1: Write the failing metadata test**

```python
import pytest

from app.core.langgraph.naver_blog_graph import NaverBlogPreviewGraph
from app.schemas.naver_blog import NaverBlogPreviewRequest


@pytest.mark.asyncio
async def test_preview_graph_includes_retry_metadata_in_response() -> None:
    graph = NaverBlogPreviewGraph()
    ...
    response = await graph.run(NaverBlogPreviewRequest(restaurant_name="을지면옥", region="중구"))

    assert "search_retry_count" in response.metadata
    assert "workflow_status" in response.metadata
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/core/langgraph/test_naver_blog_graph.py::test_preview_graph_includes_retry_metadata_in_response -v`
Expected: FAIL because metadata keys are missing

- [ ] **Step 3: Add instrumentation and structured metadata**

```python
logger.info(
    "naver_blog_preview_workflow_completed",
    restaurant_name=request.restaurant_name,
    region=request.region,
    search_query=search_query,
    search_retry_count=retry_count,
    workflow_status=status,
    fetch_success_count=fetch_success_count,
    fetch_failure_count=fetch_failure_count,
)
```

```python
metadata = {
    "search_retry_count": retry_count,
    "search_result_count": len(search_results),
    "selected_result_count": len(selected_results),
    "fetch_success_count": fetch_success_count,
    "fetch_failure_count": fetch_failure_count,
    "workflow_status": status,
}
```

- [ ] **Step 4: Run the targeted and full test suite**

Run: `uv run pytest tests/core/langgraph/test_naver_blog_graph.py tests/core/langgraph/tools/test_naver_blog_tools.py tests/api/v1/test_naver_blog.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/core/langgraph/naver_blog_graph.py tests/core/langgraph/test_naver_blog_graph.py tests/api/v1/test_naver_blog.py
git commit -m "feat: 네이버 블로그 preview 메타데이터 추가"
```

## Self-Review

### Spec coverage

- request/response schema: Task 1
- dedicated API route: Task 4
- LangGraph preview workflow: Task 3
- search/content fetch tools: Task 2
- 200 + partial failure contract: Task 3, Task 4
- Langfuse-searchable metadata: Task 5
- tests for validation/state transition/response shape: Task 1, Task 3, Task 4, Task 5

### Placeholder scan

- `TODO`, `TBD`, `implement later` 같은 placeholder 없음
- 모든 task에 파일 경로와 실행 명령 포함
- 테스트 우선 순서 유지

### Type consistency

- `NaverBlogPreviewRequest`, `NaverBlogPreviewResponse`, `NaverBlogPreviewItem`, `NaverBlogPreviewError` 이름 일관성 유지
- preview graph entrypoint는 `NaverBlogPreviewGraph.run(...)`으로 통일

Plan complete and saved to `docs/superpowers/plans/2026-05-13-naver-blog-search-preview.md`. Two execution options:

1. Subagent-Driven (recommended) - I dispatch a fresh subagent per task, review between tasks, fast iteration

2. Inline Execution - Execute tasks in this session using executing-plans, batch execution with checkpoints

사용자가 이미 구현 진행을 요청했으므로, 별도 변경 요청이 없으면 이 세션에서 `Inline Execution`으로 이어간다.
