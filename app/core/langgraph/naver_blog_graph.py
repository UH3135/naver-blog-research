"""LangGraph workflow for Naver blog preview."""

from __future__ import annotations

from typing import (
    Any,
    Callable,
)

from langgraph.graph import (
    END,
    START,
    StateGraph,
)

from app.core.langgraph.tools.naver_blog_content import fetch_naver_blog_content
from app.core.langgraph.tools.naver_blog_search import search_naver_blog
from app.core.logging import logger
from app.schemas.naver_blog import (
    NaverBlogPreviewError,
    NaverBlogPreviewItem,
    NaverBlogPreviewRequest,
    NaverBlogPreviewResponse,
    NaverBlogPreviewState,
)

try:
    from langfuse.decorators import (
        langfuse_context,
        observe,
    )
except ImportError:
    langfuse_context = None

    def observe(*args: Any, **kwargs: Any) -> Callable:
        """Fallback decorator when Langfuse decorators are unavailable."""

        def wrapper(function: Callable) -> Callable:
            return function

        return wrapper


class NaverBlogPreviewGraph:
    """Preview workflow orchestrator."""

    def __init__(self) -> None:
        """Initialize the workflow and its default tools."""
        self.search_tool: Any = search_naver_blog
        self.fetch_tool: Any = fetch_naver_blog_content
        self.graph = self._build_graph()

    def _build_graph(self) -> Any:
        """Build the compiled LangGraph preview workflow."""
        workflow = StateGraph(NaverBlogPreviewState)
        workflow.add_node("input_normalization", self._input_normalization)
        workflow.add_node("search_execution", self._search_execution)
        workflow.add_node("search_review", self._search_review)
        workflow.add_node("content_fetch", self._content_fetch)
        workflow.add_node("response_assembly", self._response_assembly)
        workflow.add_edge(START, "input_normalization")
        workflow.add_edge("input_normalization", "search_execution")
        workflow.add_edge("search_execution", "search_review")
        workflow.add_edge("search_review", "content_fetch")
        workflow.add_edge("content_fetch", "response_assembly")
        workflow.add_edge("response_assembly", END)
        return workflow.compile()

    async def _input_normalization(self, state: NaverBlogPreviewState) -> dict[str, Any]:
        """Normalize input into the primary search query."""
        search_query = f"{state.region.strip()} {state.restaurant_name.strip()} 블로그"
        logger.info(
            "naver_blog_search_query_built",
            restaurant_name=state.restaurant_name,
            region=state.region,
            search_query=search_query,
        )
        return {"search_query": search_query}

    async def _search_execution(self, state: NaverBlogPreviewState) -> dict[str, Any]:
        """Execute the primary search."""
        results = self._invoke_tool(self.search_tool, {"query": state.search_query, "max_results": state.max_results})
        logger.info("naver_blog_search_completed", search_query=state.search_query, result_count=len(results))
        return {"search_results": results}

    async def _search_review(self, state: NaverBlogPreviewState) -> dict[str, Any]:
        """Review search results and retry once if needed."""
        deduplicated_results = self._deduplicate_urls(state.search_results)
        if deduplicated_results:
            selected_results = deduplicated_results[: state.max_results]
            return {
                "selected_results": selected_results,
                "trace_metadata": {
                    "search_result_count": len(state.search_results),
                    "selected_result_count": len(selected_results),
                },
            }

        retry_count = state.search_retry_count + 1
        fallback_query = f"{state.restaurant_name.strip()} {state.region.strip()} 후기"
        logger.info("naver_blog_search_retry_started", fallback_search_query=fallback_query, retry_count=retry_count)
        fallback_results = self._invoke_tool(self.search_tool, {"query": fallback_query, "max_results": state.max_results})
        deduplicated_fallback_results = self._deduplicate_urls(fallback_results)
        if not deduplicated_fallback_results:
            return {
                "search_query": fallback_query,
                "search_retry_count": retry_count,
                "search_results": fallback_results,
                "selected_results": [],
                "errors": [
                    NaverBlogPreviewError(
                        code="search_zero_results",
                        message="No usable Naver blog search results were found",
                    )
                ],
                "trace_metadata": {
                    "fallback_search_query": fallback_query,
                    "search_result_count": 0,
                    "selected_result_count": 0,
                },
            }

        selected_results = deduplicated_fallback_results[: state.max_results]
        return {
            "search_query": fallback_query,
            "search_retry_count": retry_count,
            "search_results": fallback_results,
            "selected_results": selected_results,
            "trace_metadata": {
                "fallback_search_query": fallback_query,
                "search_result_count": len(fallback_results),
                "selected_result_count": len(selected_results),
            },
        }

    async def _content_fetch(self, state: NaverBlogPreviewState) -> dict[str, Any]:
        """Fetch preview content for selected results."""
        items: list[NaverBlogPreviewItem] = []
        errors = list(state.errors)

        for result in state.selected_results:
            try:
                fetched_item = self._invoke_tool(self.fetch_tool, {"url": result["url"]})
                fetch_status = str(fetched_item.get("fetch_status", "failed"))
                item = NaverBlogPreviewItem(
                    title=str(fetched_item.get("title") or result.get("title", "")),
                    url=str(result.get("url", "")),
                    snippet=str(result.get("snippet", "")),
                    published_at=fetched_item.get("published_at"),
                    excerpt=str(fetched_item.get("excerpt", "")),
                    raw_text_available=bool(fetched_item.get("raw_text")),
                    fetch_status=fetch_status if fetch_status in {"success", "failed"} else "failed",
                )
                items.append(item)
                if item.fetch_status == "failed":
                    errors.append(
                        NaverBlogPreviewError(
                            code="content_fetch_failed",
                            message="Failed to extract blog post content",
                            target=item.url,
                        )
                    )
            except Exception as exc:
                logger.exception("naver_blog_content_fetch_failed", url=result.get("url", ""), error=str(exc))
                items.append(
                    NaverBlogPreviewItem(
                        title=str(result.get("title", "")),
                        url=str(result.get("url", "")),
                        snippet=str(result.get("snippet", "")),
                        excerpt="",
                        raw_text_available=False,
                        fetch_status="failed",
                    )
                )
                errors.append(
                    NaverBlogPreviewError(
                        code="content_fetch_failed",
                        message=str(exc),
                        target=str(result.get("url", "")),
                    )
                )

        return {"fetched_items": items, "errors": errors}

    async def _response_assembly(self, state: NaverBlogPreviewState) -> dict[str, Any]:
        """Compute final workflow status and metadata."""
        fetch_success_count = len([item for item in state.fetched_items if item.fetch_status == "success"])
        fetch_failure_count = len([item for item in state.fetched_items if item.fetch_status == "failed"])

        if fetch_success_count > 0 and fetch_failure_count == 0 and not state.errors:
            status = "success"
        elif fetch_success_count > 0:
            status = "partial_success"
        else:
            status = "failed"

        metadata = {
            "search_retry_count": state.search_retry_count,
            "search_result_count": state.trace_metadata.get("search_result_count", len(state.search_results)),
            "selected_result_count": state.trace_metadata.get("selected_result_count", len(state.selected_results)),
            "fetch_success_count": fetch_success_count,
            "fetch_failure_count": fetch_failure_count,
            "workflow_status": status,
        }
        if "fallback_search_query" in state.trace_metadata:
            metadata["fallback_search_query"] = state.trace_metadata["fallback_search_query"]

        logger.info(
            "naver_blog_preview_workflow_completed",
            restaurant_name=state.restaurant_name,
            region=state.region,
            search_query=state.search_query,
            search_retry_count=state.search_retry_count,
            workflow_status=status,
            fetch_success_count=fetch_success_count,
            fetch_failure_count=fetch_failure_count,
        )

        if langfuse_context is not None:
            langfuse_context.update_current_observation(
                input={
                    "restaurant_name": state.restaurant_name,
                    "region": state.region,
                    "search_query": state.search_query,
                },
                output=metadata,
                metadata=metadata,
            )

        return {"status": status, "trace_metadata": metadata}

    def _invoke_tool(self, tool_instance: Any, payload: dict[str, Any]) -> Any:
        """Invoke a tool-like object or a plain callable."""
        if hasattr(tool_instance, "invoke"):
            return tool_instance.invoke(payload)
        return tool_instance(payload)

    def _deduplicate_urls(self, results: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Deduplicate results by URL while preserving order."""
        deduplicated_results: list[dict[str, Any]] = []
        seen_urls: set[str] = set()
        for result in results:
            url = str(result.get("url", "")).strip()
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)
            deduplicated_results.append(result)
        return deduplicated_results

    @observe(name="naver_blog_preview_workflow")
    async def run(self, request: NaverBlogPreviewRequest) -> NaverBlogPreviewResponse:
        """Run the preview workflow and return an API response."""
        initial_state = NaverBlogPreviewState(
            restaurant_name=request.restaurant_name,
            region=request.region,
            max_results=request.max_results,
        )
        final_state = await self.graph.ainvoke(initial_state)
        return NaverBlogPreviewResponse(
            status=final_state["status"],
            query={"restaurant_name": request.restaurant_name, "region": request.region},
            search_query=final_state["search_query"],
            items=final_state["fetched_items"],
            errors=final_state["errors"],
            metadata=final_state["trace_metadata"],
        )
