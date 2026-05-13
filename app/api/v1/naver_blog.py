"""Naver blog preview API endpoints."""

from fastapi import (
    APIRouter,
    Depends,
    Request,
)

from app.api.v1.auth import get_current_session
from app.core.config import settings
from app.core.langgraph.naver_blog_graph import NaverBlogPreviewGraph
from app.core.limiter import limiter
from app.core.logging import logger
from app.models.session import Session
from app.schemas.naver_blog import (
    NaverBlogPreviewRequest,
    NaverBlogPreviewResponse,
)

router = APIRouter()
preview_graph = NaverBlogPreviewGraph()


@router.post("/search-preview", response_model=NaverBlogPreviewResponse)
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["naver_blog_preview"][0])
async def search_preview(
    request: Request,
    preview_request: NaverBlogPreviewRequest,
    session: Session = Depends(get_current_session),
) -> NaverBlogPreviewResponse:
    """Run the Naver blog preview workflow."""
    logger.info(
        "naver_blog_preview_request_received",
        session_id=session.id,
        restaurant_name=preview_request.restaurant_name,
        region=preview_request.region,
        max_results=preview_request.max_results,
    )
    return await preview_graph.run(preview_request)
