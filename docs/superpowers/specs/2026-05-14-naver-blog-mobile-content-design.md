# Naver Blog Mobile Content Extraction Design

## Goal

Replace the production Naver blog content fetcher with the mobile-page extraction strategy that successfully reads current Naver blog posts, while keeping the existing API response contract unchanged.

## Scope

The change is limited to `app/core/langgraph/tools/naver_blog_content.py` and its tests. The router, graph workflow, schemas, and search parser keep their current interfaces. Debug scripts are not used by production code and can be removed separately.

## Design

`fetch_naver_blog_content(url)` keeps returning `title`, `url`, `published_at`, `raw_text`, `excerpt`, and `fetch_status`. Internally it normalizes PC blog post URLs to `https://m.blog.naver.com/{blog_id}/{post_id}`, loads the mobile HTML with retry, extracts the title and published date, then reads the body from known mobile containers.

The body parser avoids the existing short non-greedy regex problem by locating a container marker such as `se-main-container` and extracting the balanced outer `<div>`. It then removes scripts/styles, converts block and break tags to line breaks, strips tags, unescapes entities, and normalizes whitespace.

## Error Handling

Network loading continues to use tenacity retry. Non-Naver URLs raise `ValueError`. If the page loads but no body text can be extracted, the tool returns `fetch_status="failed"` with empty `raw_text` and `excerpt`, allowing `NaverBlogPreviewGraph` to use its existing partial-failure path.

## Testing

Unit tests cover PC-to-mobile URL normalization, balanced nested body extraction, and the public fetch tool using a monkeypatched HTML loader. Existing graph/API tests should continue to pass because response fields and status values do not change.
