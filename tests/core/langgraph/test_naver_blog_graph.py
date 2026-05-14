"""Tests for the Naver blog preview workflow."""

import asyncio

from app.core.langgraph.naver_blog_graph import NaverBlogPreviewGraph
from app.schemas.naver_blog import NaverBlogPreviewRequest


def test_preview_graph_retries_once_when_search_results_are_empty() -> None:
    """The workflow should retry once with a fallback query."""
    graph = NaverBlogPreviewGraph()

    invoked_queries: list[str] = []

    def fake_search_tool(payload: dict[str, str | int]) -> list[dict[str, str]]:
        invoked_queries.append(str(payload["query"]))
        return []

    graph.search_tool = fake_search_tool
    response = asyncio.run(graph.run(NaverBlogPreviewRequest(restaurant_name="을지면옥", region="중구")))

    assert response.status == "failed"
    assert response.metadata["search_retry_count"] == 1
    assert len(invoked_queries) == 2


def test_preview_graph_includes_retry_metadata_in_response() -> None:
    """Workflow responses should include structured execution metadata."""
    graph = NaverBlogPreviewGraph()

    def fake_search_tool(payload: dict[str, str | int]) -> list[dict[str, str]]:
        return [
            {
                "title": "테스트 블로그",
                "url": "https://blog.naver.com/test/1",
                "snippet": "요약",
                "published_at": "",
            }
        ]

    def fake_fetch_tool(payload: dict[str, str]) -> dict[str, str]:
        return {
            "title": "테스트 블로그",
            "url": payload["url"],
            "published_at": "2026-05-13",
            "raw_text": "본문",
            "excerpt": "본문",
            "fetch_status": "success",
        }

    graph.search_tool = fake_search_tool
    graph.fetch_tool = fake_fetch_tool

    response = asyncio.run(graph.run(NaverBlogPreviewRequest(restaurant_name="을지면옥", region="중구")))

    assert response.status == "success"
    assert "search_retry_count" in response.metadata
    assert "workflow_status" in response.metadata
