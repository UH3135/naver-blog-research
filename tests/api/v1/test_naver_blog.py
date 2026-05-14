"""Tests for the Naver blog preview API."""

import os
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient
from pydantic import ValidationError

os.environ["APP_ENV"] = "production"

from app.api.v1.auth import get_current_session
from app.api.v1.naver_blog import preview_graph
from app.main import app
from app.models.session import Session
from app.schemas.naver_blog import NaverBlogPreviewResponse
from app.schemas.naver_blog import NaverBlogPreviewRequest


def test_naver_blog_preview_request_rejects_blank_region() -> None:
    """Blank region should be rejected by the request schema."""
    try:
        NaverBlogPreviewRequest(restaurant_name="을지면옥", region="   ")
    except ValidationError as exc:
        assert "region" in str(exc)
    else:
        raise AssertionError("ValidationError was not raised")


def test_naver_blog_preview_rejects_blank_restaurant_name() -> None:
    """Blank restaurant name should be rejected by the route."""

    async def fake_get_current_session() -> Session:
        return Session(id="session-1", user_id=1, name="test")

    app.dependency_overrides[get_current_session] = fake_get_current_session
    client = TestClient(app)

    response = client.post(
        "/api/v1/naver-blog/search-preview",
        json={"restaurant_name": " ", "region": "중구"},
        headers={"Authorization": "Bearer test-token"},
    )

    app.dependency_overrides.clear()

    assert response.status_code == 422


def test_naver_blog_preview_returns_structured_success_response() -> None:
    """Route should return the preview graph response body."""

    async def fake_get_current_session() -> Session:
        return Session(id="session-1", user_id=1, name="test")

    preview_graph.run = AsyncMock(
        return_value=NaverBlogPreviewResponse(
            status="success",
            query={"restaurant_name": "을지면옥", "region": "중구"},
            search_query="중구 을지면옥 블로그",
            items=[],
            errors=[],
            metadata={"search_retry_count": 0, "workflow_status": "success"},
        )
    )
    app.dependency_overrides[get_current_session] = fake_get_current_session
    client = TestClient(app)

    response = client.post(
        "/api/v1/naver-blog/search-preview",
        json={"restaurant_name": "을지면옥", "region": "중구"},
        headers={"Authorization": "Bearer test-token"},
    )

    app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["status"] == "success"
    assert response.json()["metadata"]["workflow_status"] == "success"
