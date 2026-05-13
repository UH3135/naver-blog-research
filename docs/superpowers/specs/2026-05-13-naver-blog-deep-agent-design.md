# Naver Blog Deep Agent Design

## Overview

This document defines the first implementation slice for a Naver blog restaurant analysis capability built on top of the current FastAPI and LangGraph project.

The long-term goal is to expose the capability through API first, then reuse the same workflow from MCP, Discord, and other messaging surfaces. The core of the system is a LangGraph-based deep agent that orchestrates tool execution, validates intermediate results, and emits traceable workflow state to Langfuse.

The first slice is intentionally narrow. It does not yet score restaurants or decide whether a restaurant is good or bad. It proves that the agent can:

1. accept `restaurant_name` and `region`
2. search Naver blog posts related to that restaurant
3. fetch blog post content for selected results
4. return structured preview data
5. record the workflow in Langfuse so failures and retries are searchable

## Goals

The first implementation must:

1. provide a dedicated API endpoint for Naver blog search preview
2. execute a LangGraph workflow instead of a direct service call
3. use Python-based tools for Naver blog search and blog content fetching
4. return structured search and content preview data
5. support partial success without failing the entire HTTP request
6. emit searchable traces and metadata to Langfuse

## Non-Goals

The first implementation does not include:

1. ad detection and exclusion
2. positive and negative review summarization
3. free-form natural language restaurant discovery
4. Discord or MCP integration
5. long-term memory for search results
6. production-grade ranking heuristics beyond basic query normalization and retry

These are expected follow-up phases and should not be folded into the first implementation plan.

## User Input and API Shape

The first endpoint should be a dedicated API route outside the chatbot route group. A representative path is:

`POST /api/v1/naver-blog/search-preview`

The request model uses Pydantic validation and accepts:

- `restaurant_name: str`
- `region: str`
- `max_results: int | None`

Validation rules:

1. `restaurant_name` is required and non-empty
2. `region` is required and non-empty
3. `max_results` is optional but must stay inside a safe upper bound

Validation failures should be rejected by FastAPI and Pydantic with `422`.

## Response Contract

The endpoint should always try to return `200` for workflow execution outcomes, even when the workflow partially fails or fully fails after the request passed validation.

Response fields:

- `status`
  - `success`
  - `partial_success`
  - `failed`
- `query`
  - normalized input values
- `search_query`
  - final query string used by the workflow
- `items`
  - collected and optionally fetched blog results
- `errors`
  - structured workflow errors
- `metadata`
  - counts, retry information, and execution summary

Each item should include at least:

- `title`
- `url`
- `snippet`
- `blogger_name`
- `published_at`
- `excerpt`
- `raw_text_available`
- `fetch_status`

The first version should default to returning only excerpted content instead of the full raw body. This keeps payloads small and makes the preview endpoint safer to inspect.

## Workflow Architecture

The system should use a dedicated LangGraph workflow rather than embedding the full logic inside the API route.

The graph is composed of five responsibilities:

1. `input_normalization`
2. `search_execution`
3. `search_review`
4. `content_fetch`
5. `response_assembly`

### 1. Input Normalization Node

Inputs:

- `restaurant_name`
- `region`
- `max_results`

Responsibilities:

1. trim and normalize user input
2. generate the initial search query
3. store normalized values in graph state

Recommended first query pattern:

- `"{region} {restaurant_name} 블로그"`

This node does not call any external system.

### 2. Search Execution Node

Responsibilities:

1. invoke the `search_naver_blog` tool
2. collect raw candidate blog results
3. persist search output in graph state

This node should be instrumented so Langfuse captures:

- normalized search query
- number of results returned
- execution duration

### 3. Search Review Node

Responsibilities:

1. validate that search output is structurally usable
2. deduplicate obvious duplicate URLs
3. select the subset to pass to the fetch step
4. decide whether one retry is needed

Retry policy for the first implementation:

1. if zero usable results are returned, perform one retry with a fallback query
2. fallback query example:
   `"{restaurant_name} {region} 후기"`
3. if the retry also returns zero usable results, finish with `status=failed`

This node is the first self-recovery layer. It is intentionally bounded to one fallback attempt.

### 4. Content Fetch Node

Responsibilities:

1. invoke `fetch_naver_blog_content` for selected URLs
2. capture per-URL success or failure
3. store fetched content and structured errors in graph state

The first implementation should fetch a limited number of top candidates. The exact default can be finalized in planning, but the design assumes a small number such as `3` to `5` for easier debugging and lower external load.

If some URLs fail and some succeed, the workflow should continue and end as `partial_success`.

### 5. Response Assembly Node

Responsibilities:

1. build the API response shape
2. compute final status
3. include execution metadata and structured errors

Status rules:

1. `success` when search and at least one content fetch succeeds without any recorded fetch error
2. `partial_success` when at least one item succeeds and at least one error is present
3. `failed` when no usable items are available after the workflow finishes

## Graph State Design

The workflow needs a dedicated state schema separate from the existing chatbot state.

State should include:

- `restaurant_name`
- `region`
- `max_results`
- `search_query`
- `search_retry_count`
- `search_results`
- `selected_results`
- `fetched_items`
- `errors`
- `status`
- `trace_metadata`

This state should be implemented with a Pydantic model or typed schema consistent with existing LangGraph usage in the project.

## Tool Design

The first implementation needs two Python tools.

### Tool 1: `search_naver_blog`

Input:

- `query: str`
- `max_results: int`

Output:

- list of blog search candidates in normalized shape

Each result should include:

- `title`
- `url`
- `snippet`
- `blogger_name`
- `published_at` when available

Responsibilities:

1. execute Naver blog search
2. parse the result page or response payload
3. normalize result fields
4. return structured data without embedding presentation logic

### Tool 2: `fetch_naver_blog_content`

Input:

- `url: str`

Output:

- normalized blog content payload

Fields:

- `title`
- `url`
- `published_at`
- `raw_text`
- `excerpt`
- `fetch_status`

Responsibilities:

1. load the target blog page
2. extract meaningful textual content
3. produce a short excerpt for preview responses
4. expose structured failure information when extraction fails

## Tool Boundary Rules

To keep the architecture reusable:

1. tools handle external access and extraction only
2. graph nodes decide sequencing, retry, and status transitions
3. API routes validate input and return response models only

This separation is important because later phases will add ad filtering and review summarization without changing the search and fetch tool contracts.

## Failure Handling Policy

The system should separate validation failure from workflow failure.

### Validation Failure

Handled by Pydantic and FastAPI.

Behavior:

- return `422`
- do not enter the workflow

### Workflow Partial Failure

Examples:

- search succeeds but one content fetch fails
- search retry succeeds but some items remain unusable

Behavior:

- return `200`
- set `status=partial_success`
- include successful items
- include structured errors with failure reasons

### Workflow Complete Failure

Examples:

- zero usable search results after retry
- all content fetch attempts fail

Behavior:

- return `200`
- set `status=failed`
- return empty or unusable items list
- include structured errors explaining why the workflow failed

## Self-Recovery Policy

The deep agent behavior in the first slice is intentionally narrow and deterministic.

Self-recovery includes:

1. one fallback search query when the first query yields zero usable results
2. one bounded retry path recorded in graph state
3. continuation on partial fetch success instead of aborting the workflow

Self-recovery does not yet include:

1. open-ended autonomous planning
2. ad-judgment loops
3. multi-hop exploratory search

This keeps the first workflow explainable and easier to debug while still proving the orchestration model.

## Langfuse Observability

The workflow must emit traceable metadata to Langfuse for both success and failure cases.

Each request should produce one top-level trace for the preview workflow. Nodes and tool calls should contribute searchable metadata such as:

- `restaurant_name`
- `region`
- `search_query`
- `fallback_search_query`
- `search_result_count`
- `selected_result_count`
- `fetch_success_count`
- `fetch_failure_count`
- `workflow_status`

Per-URL failures should also be captured in a structured way when possible so operators can inspect why extraction failed.

Langfuse instrumentation should follow existing project patterns and must be attached to LLM and workflow execution paths consistently.

## Logging and Error Handling

Project rules from `AGENTS.md` apply:

1. use `structlog`
2. use lowercase underscore event names
3. avoid f-strings in event messages
4. use `logger.exception()` for exception cases that need tracebacks

Representative event examples:

- `naver_blog_preview_request_received`
- `naver_blog_search_started`
- `naver_blog_search_completed`
- `naver_blog_search_retry_started`
- `naver_blog_content_fetch_failed`
- `naver_blog_preview_workflow_completed`

## API Layer Design

The route should live in a dedicated router module under `app/api/v1/`.

The API layer should:

1. accept the request model
2. depend on authenticated session if the product keeps the current auth requirement
3. invoke the new preview workflow service or graph adapter
4. return a typed response model
5. apply the standard rate limiting decorator

The route should not contain scraping logic, parsing logic, or graph node logic.

## Reusability for MCP and Messaging Channels

The workflow should be treated as a reusable capability, not as a one-off route implementation.

That means three layers:

1. Python tools for Naver search and content extraction
2. LangGraph workflow for orchestration and bounded self-recovery
3. channel adapters such as FastAPI now, MCP or Discord later

Future channels should call the same workflow or service boundary rather than reimplementing search logic.

## Prompt and Skill Context Strategy

The workflow should be describable as a reusable execution policy in prompt context or a skill-like instruction bundle.

The reusable context for this phase should state:

1. inputs are limited to `restaurant_name` and `region`
2. the workflow objective is search preview and content fetch preview only
3. ad detection is not active in this phase
4. workflow failures are communicated through `status` and `errors`
5. the agent may perform one bounded fallback search retry

This lets later agent surfaces reuse the same operating rules without coupling them to API-specific behavior.

## Testing Strategy

The first implementation plan should include at least:

1. request model validation tests
2. response model tests
3. graph node unit tests for status transitions
4. tool contract tests with mocked external responses when stable fixtures are available
5. integration tests for:
   - search success
   - partial fetch failure
   - complete failure after retry

Tests should focus on deterministic workflow behavior and typed response shape rather than on live Naver reliability.

## Security and Operational Notes

Because this workflow touches external content collection:

1. cap `max_results`
2. sanitize and normalize user input before building the search query
3. avoid caching failures as successful results
4. keep timeout and retry behavior explicit at the tool layer
5. preserve observability for rate, latency, and failure reasons

## Follow-Up Phases

After this first slice is stable, the next planned phases are:

1. ad detection and conservative exclusion
2. positive and negative review summarization
3. free-form query support through auxiliary search tools
4. MCP and messaging channel integration
5. richer ranking and evaluation logic

## Recommended Planning Boundary

The next implementation plan should stay strictly inside this scope:

1. new request and response schemas
2. dedicated API route
3. LangGraph preview workflow and state schema
4. search and content-fetch tool implementations
5. Langfuse instrumentation for searchable workflow traces
6. basic tests for validation, workflow state transitions, and response shaping

Anything beyond this should be deferred to a later plan.
