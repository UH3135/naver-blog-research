"""Schemas for the Naver blog preview workflow."""

from typing import (
    Any,
    Literal,
    Optional,
)

from pydantic import (
    BaseModel,
    Field,
    field_validator,
)


class NaverBlogPreviewRequest(BaseModel):
    """Request model for Naver blog preview."""

    restaurant_name: str = Field(..., min_length=1, max_length=200)
    region: str = Field(..., min_length=1, max_length=200)
    max_results: int = Field(default=3, ge=1, le=5)

    @field_validator("restaurant_name", "region")
    @classmethod
    def validate_non_blank(cls, value: str) -> str:
        """Reject blank string values after trimming."""
        stripped_value = value.strip()
        if not stripped_value:
            raise ValueError("value must not be blank")
        return stripped_value


class NaverBlogPreviewError(BaseModel):
    """Structured workflow error."""

    code: str
    message: str
    target: Optional[str] = None


class NaverBlogPreviewItem(BaseModel):
    """Structured preview item."""

    title: str
    url: str
    snippet: str = ""
    blogger_name: str = ""
    published_at: Optional[str] = None
    excerpt: str = ""
    raw_text_available: bool = False
    fetch_status: Literal["pending", "success", "failed"] = "pending"


class NaverBlogPreviewResponse(BaseModel):
    """API response for the preview workflow."""

    status: Literal["success", "partial_success", "failed"]
    query: dict[str, str]
    search_query: str
    items: list[NaverBlogPreviewItem]
    errors: list[NaverBlogPreviewError] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class NaverBlogPreviewState(BaseModel):
    """LangGraph state for the preview workflow."""

    restaurant_name: str
    region: str
    max_results: int
    search_query: str = ""
    search_retry_count: int = 0
    search_results: list[dict[str, Any]] = Field(default_factory=list)
    selected_results: list[dict[str, Any]] = Field(default_factory=list)
    fetched_items: list[NaverBlogPreviewItem] = Field(default_factory=list)
    errors: list[NaverBlogPreviewError] = Field(default_factory=list)
    status: Literal["success", "partial_success", "failed"] = "failed"
    trace_metadata: dict[str, Any] = Field(default_factory=dict)
