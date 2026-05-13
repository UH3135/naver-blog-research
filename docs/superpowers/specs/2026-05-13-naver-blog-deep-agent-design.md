# 네이버 블로그 딥 에이전트 설계

## 개요

이 문서는 현재 FastAPI + LangGraph 프로젝트 위에 구축할 네이버 블로그 기반 식당 분석 기능의 1차 구현 범위를 정의한다.

장기 목표는 이 기능을 우선 API로 제공하는 것이다. 시스템의 핵심은 LangGraph 기반 딥 에이전트이며, 이 에이전트는 도구 실행을 오케스트레이션하고, 중간 결과를 검증하며, 워크플로우 상태를 Langfuse에 추적 가능하게 남긴다.

이번 1차 범위는 의도적으로 좁게 잡는다. 아직 식당의 좋고 나쁨을 판단하거나 점수를 매기지 않는다. 이번 단계에서 검증할 것은 아래 다섯 가지다.

1. `restaurant_name`과 `region`을 입력받는다.
2. 해당 식당과 관련된 네이버 블로그 게시글을 검색한다.
3. 선택된 블로그 게시글의 본문을 수집한다.
4. 구조화된 미리보기 데이터를 반환한다.
5. 실패와 재시도를 Langfuse에서 검색할 수 있도록 워크플로우를 기록한다.

## 목표

1차 구현은 아래를 만족해야 한다.

1. 네이버 블로그 검색 미리보기를 위한 전용 API 엔드포인트를 제공한다.
2. 단순 서비스 호출이 아니라 LangGraph 워크플로우를 실행한다.
3. 네이버 블로그 검색과 본문 수집을 Python 기반 도구로 구현한다.
4. 구조화된 검색 결과와 본문 미리보기 데이터를 반환한다.
5. 일부 수집 실패가 있어도 HTTP 요청 전체를 실패시키지 않는다.
6. Langfuse에서 검색 가능한 trace와 메타데이터를 남긴다.

## 비목표

이번 1차 구현에는 아래 항목을 포함하지 않는다.

1. 광고성 여부 판별 및 제외
2. 좋은 평가와 나쁜 평가 요약
3. 자유문장 기반 식당 탐색
4. 외부 채널 연동
5. 검색 결과에 대한 장기 메모리 저장
6. 기본적인 질의 정규화와 재시도 이상의 고도화된 랭킹 로직

이 항목들은 후속 단계로 다루며, 이번 구현 계획 범위에 섞어 넣지 않는다.

## 사용자 입력과 API 형태

1차 엔드포인트는 기존 chatbot 라우트와 분리된 전용 API로 둔다. 대표적인 경로 예시는 아래와 같다.

`POST /api/v1/naver-blog/search-preview`

요청 모델은 Pydantic validation을 사용하며 아래 필드를 받는다.

- `restaurant_name: str`
- `region: str`
- `max_results: int | None`

검증 규칙은 아래와 같다.

1. `restaurant_name`은 필수이며 비어 있으면 안 된다.
2. `region`은 필수이며 비어 있으면 안 된다.
3. `max_results`는 선택값이지만 안전한 상한선 안에 있어야 한다.

입력 검증 실패는 FastAPI와 Pydantic에 의해 `422`로 처리한다.

## 응답 계약

요청이 validation을 통과한 뒤에는, 워크플로우 실행 중 일부 실패나 전체 실패가 발생하더라도 최대한 `200` 응답을 반환하도록 설계한다.

응답 필드는 아래를 포함한다.

- `status`
  - `success`
  - `partial_success`
  - `failed`
- `query`
  - 정규화된 입력값
- `search_query`
  - 워크플로우에서 실제 사용한 검색어
- `items`
  - 수집되었고 필요시 본문까지 가져온 블로그 결과 목록
- `errors`
  - 구조화된 워크플로우 에러 정보
- `metadata`
  - 개수, 재시도 여부, 실행 요약 정보

각 `item`은 최소한 아래 필드를 포함한다.

- `title`
- `url`
- `snippet`
- `blogger_name`
- `published_at`
- `excerpt`
- `raw_text_available`
- `fetch_status`

1차 버전은 기본적으로 본문 전체 `raw_text` 대신 `excerpt`만 응답에 포함한다. 이렇게 하면 payload 크기를 줄일 수 있고, preview 엔드포인트로 다루기에도 안전하다.

## 워크플로우 아키텍처

전체 로직은 API 라우트 내부에 직접 넣지 않고, 전용 LangGraph 워크플로우로 구성한다.

그래프는 크게 다섯 가지 책임으로 나눈다.

1. `input_normalization`
2. `search_execution`
3. `search_review`
4. `content_fetch`
5. `response_assembly`

### 1. Input Normalization Node

입력:

- `restaurant_name`
- `region`
- `max_results`

책임:

1. 사용자 입력을 trim하고 정규화한다.
2. 초기 검색 질의를 생성한다.
3. 정규화된 값을 graph state에 저장한다.

권장 초기 검색 질의 패턴:

- `"{region} {restaurant_name} 블로그"`

이 노드는 외부 시스템을 호출하지 않는다.

### 2. Search Execution Node

책임:

1. `search_naver_blog` 도구를 호출한다.
2. 원본 후보 블로그 결과를 수집한다.
3. 검색 결과를 graph state에 저장한다.

이 노드는 Langfuse에 아래 정보를 남길 수 있어야 한다.

- 정규화된 검색 질의
- 반환된 결과 수
- 실행 시간

### 3. Search Review Node

책임:

1. 검색 결과가 구조적으로 사용 가능한지 검증한다.
2. 명백한 중복 URL을 제거한다.
3. 다음 단계로 넘길 대상만 선택한다.
4. 한 번의 재시도가 필요한지 판단한다.

1차 구현의 재시도 정책:

1. 사용 가능한 결과가 0건이면 fallback 질의로 한 번 재시도한다.
2. fallback 질의 예시:
   `"{restaurant_name} {region} 후기"`
3. 재시도 후에도 사용 가능한 결과가 0건이면 `status=failed`로 종료한다.

이 노드는 첫 번째 자기회복 계층이다. 다만 1차에서는 한 번의 fallback 시도로 제한한다.

### 4. Content Fetch Node

책임:

1. 선택된 URL들에 대해 `fetch_naver_blog_content` 도구를 호출한다.
2. URL별 성공과 실패를 기록한다.
3. 수집된 본문과 구조화된 에러를 graph state에 저장한다.

1차 구현은 상위 일부 결과만 제한적으로 본문을 수집한다. 정확한 기본값은 구현 계획에서 확정하되, 설계상으로는 디버깅과 외부 부하를 고려해 `3`에서 `5`개 정도의 작은 수를 가정한다.

일부 URL은 실패하고 일부는 성공하더라도 워크플로우는 계속 진행하며 최종 상태는 `partial_success`가 된다.

### 5. Response Assembly Node

책임:

1. API 응답 형태를 구성한다.
2. 최종 상태값을 계산한다.
3. 실행 메타데이터와 구조화된 에러를 포함한다.

상태 규칙:

1. 검색이 성공하고, 최소 1개 이상의 본문 수집이 성공했으며, 별도 fetch 에러가 없으면 `success`
2. 최소 1개 이상의 성공 결과가 있지만 에러도 함께 존재하면 `partial_success`
3. 워크플로우 종료 시점에 사용 가능한 결과가 하나도 없으면 `failed`

## Graph State 설계

이 워크플로우는 기존 chatbot state와 분리된 전용 상태 스키마가 필요하다.

state는 아래 필드를 포함한다.

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

이 state는 현재 프로젝트의 LangGraph 사용 방식에 맞춰 Pydantic model 또는 typed schema로 구현한다.

## 도구 설계

1차 구현에는 두 개의 Python 도구가 필요하다.

### 도구 1: `search_naver_blog`

입력:

- `query: str`
- `max_results: int`

출력:

- 정규화된 형태의 블로그 검색 후보 목록

각 결과는 아래 필드를 포함한다.

- `title`
- `url`
- `snippet`
- `blogger_name`
- `published_at` 가능 시 포함

책임:

1. 네이버 블로그 검색을 수행한다.
2. 결과 페이지 또는 응답 payload를 파싱한다.
3. 결과 필드를 정규화한다.
4. 표현 로직을 섞지 않고 구조화된 데이터를 반환한다.

### 도구 2: `fetch_naver_blog_content`

입력:

- `url: str`

출력:

- 정규화된 블로그 본문 payload

필드:

- `title`
- `url`
- `published_at`
- `raw_text`
- `excerpt`
- `fetch_status`

책임:

1. 대상 블로그 페이지를 불러온다.
2. 의미 있는 텍스트 본문을 추출한다.
3. preview 응답에 사용할 짧은 excerpt를 생성한다.
4. 추출 실패 시 구조화된 실패 정보를 노출한다.

## 도구 경계 규칙

아키텍처를 재사용 가능하게 유지하기 위해 아래 원칙을 따른다.

1. 도구는 외부 접근과 추출만 담당한다.
2. graph node는 순서 제어, 재시도, 상태 전이를 담당한다.
3. API route는 입력 검증과 응답 모델 반환만 담당한다.

이 분리가 중요한 이유는, 다음 단계에서 광고 판별과 리뷰 요약이 추가되더라도 검색과 본문 수집 도구 계약은 그대로 유지할 수 있어야 하기 때문이다.

## 실패 처리 정책

이 시스템은 validation 실패와 workflow 실패를 분리해서 다룬다.

### Validation Failure

Pydantic와 FastAPI가 처리한다.

동작:

- `422` 반환
- 워크플로우에 진입하지 않음

### Workflow Partial Failure

예시:

- 검색은 성공했지만 일부 본문 수집이 실패한 경우
- 검색 재시도는 성공했지만 일부 결과가 여전히 사용 불가능한 경우

동작:

- `200` 반환
- `status=partial_success`
- 성공한 item 포함
- 실패 이유를 구조화한 `errors` 포함

### Workflow Complete Failure

예시:

- 재시도 후에도 사용 가능한 검색 결과가 0건인 경우
- 모든 본문 수집이 실패한 경우

동작:

- `200` 반환
- `status=failed`
- 비어 있거나 사용 불가능한 items 목록 반환
- 왜 실패했는지 설명하는 구조화된 `errors` 포함

## 자기회복 정책

이번 1차에서의 딥 에이전트 동작은 의도적으로 좁고 결정적인 흐름으로 제한한다.

자기회복에는 아래가 포함된다.

1. 첫 검색 질의에서 사용 가능한 결과가 없을 때 fallback 검색 질의 1회 수행
2. graph state에 기록되는 제한된 재시도 경로
3. 일부 본문 수집 성공 시 전체 워크플로우를 중단하지 않고 계속 진행

이번 단계에서 아직 포함하지 않는 자기회복 범위:

1. 개방형 자율 계획
2. 광고성 판단 루프
3. 다단계 탐색형 검색

이렇게 하면 1차 워크플로우를 설명 가능하고 디버그하기 쉬운 형태로 유지하면서도, 오케스트레이션 모델 자체는 검증할 수 있다.

## Langfuse 관측 가능성

워크플로우는 성공과 실패 모두에 대해 Langfuse에 추적 가능한 메타데이터를 남겨야 한다.

각 요청은 preview 워크플로우에 대한 하나의 top-level trace를 생성해야 하며, 노드와 도구 호출은 아래와 같은 검색 가능한 메타데이터를 남길 수 있어야 한다.

- `restaurant_name`
- `region`
- `search_query`
- `fallback_search_query`
- `search_result_count`
- `selected_result_count`
- `fetch_success_count`
- `fetch_failure_count`
- `workflow_status`

가능하다면 URL별 실패 원인도 구조화된 형태로 남겨서, 운영자가 Langfuse 안에서 바로 어떤 추출이 왜 실패했는지 확인할 수 있게 한다.

Langfuse instrumentation은 현재 프로젝트의 기존 패턴을 따르며, LLM 호출과 워크플로우 실행 경로에 일관되게 부착해야 한다.

## 로깅과 에러 처리

`AGENTS.md`의 프로젝트 규칙을 그대로 따른다.

1. `structlog` 사용
2. 이벤트 이름은 lowercase underscore 사용
3. 이벤트 메시지에 f-string 사용 금지
4. traceback이 필요한 예외는 `logger.exception()` 사용

대표적인 이벤트 예시:

- `naver_blog_preview_request_received`
- `naver_blog_search_started`
- `naver_blog_search_completed`
- `naver_blog_search_retry_started`
- `naver_blog_content_fetch_failed`
- `naver_blog_preview_workflow_completed`

## API 레이어 설계

라우트는 `app/api/v1/` 아래의 전용 router module에 둔다.

API 레이어는 아래만 담당한다.

1. 요청 모델 수신
2. 현재 제품 정책상 인증이 필요하다면 session dependency 적용
3. 새 preview workflow service 또는 graph adapter 호출
4. 타입이 있는 response model 반환
5. 표준 rate limiting decorator 적용

라우트 안에 스크래핑 로직, 파싱 로직, graph node 로직을 넣지 않는다.

## 프롬프트 및 스킬 컨텍스트 전략

이 워크플로우는 재사용 가능한 실행 정책으로 프롬프트 컨텍스트 또는 skill-like instruction bundle에 설명될 수 있어야 한다.

이번 단계에서 재사용 컨텍스트는 아래 규칙을 담아야 한다.

1. 입력은 `restaurant_name`과 `region`으로 제한한다.
2. 워크플로우 목표는 검색 preview와 본문 수집 preview까지다.
3. 광고성 판별은 아직 비활성 상태다.
4. 실패는 `status`와 `errors`로 해석한다.
5. 에이전트는 제한된 fallback 검색 재시도 1회를 수행할 수 있다.

이렇게 하면 이후에 프롬프트 컨텍스트나 스킬 형태로 확장하더라도 동일한 운영 규칙을 재사용할 수 있다.

## 테스트 전략

1차 구현 계획에는 최소한 아래 테스트가 포함되어야 한다.

1. 요청 모델 validation 테스트
2. 응답 모델 테스트
3. 상태 전이에 대한 graph node 단위 테스트
4. 안정적인 fixture를 만들 수 있다면 mocked external response 기반 도구 계약 테스트
5. 아래 경우에 대한 integration test
   - 검색 성공
   - 일부 본문 수집 실패
   - 재시도 후 전체 실패

테스트는 라이브 네이버 환경의 안정성보다, 결정적인 워크플로우 동작과 타입이 있는 응답 형태 검증에 집중해야 한다.

## 보안 및 운영 메모

외부 컨텐츠 수집이 포함되므로 아래 운영 원칙을 둔다.

1. `max_results` 상한선을 둔다.
2. 검색 질의를 만들기 전에 사용자 입력을 sanitize 및 normalize 한다.
3. 실패 결과를 성공 응답처럼 캐싱하지 않는다.
4. timeout과 retry 동작을 도구 레이어에서 명시적으로 관리한다.
5. 요청 수, 지연 시간, 실패 이유에 대한 관측 가능성을 유지한다.

## 후속 단계

이번 1차 범위가 안정화된 뒤의 다음 단계는 아래와 같다.

1. 광고성 판별 및 보수적 제외
2. 좋은 평가와 나쁜 평가 요약
3. 보조 검색 도구를 통한 자유문장 입력 지원
4. 프롬프트 컨텍스트 또는 스킬 형태 확장
5. 더 정교한 랭킹 및 평가 로직

## 권장 구현 계획 경계

다음 구현 계획은 아래 범위 안에 엄격하게 머물러야 한다.

1. 새로운 request/response schema
2. 전용 API route
3. LangGraph preview workflow와 state schema
4. search 및 content-fetch 도구 구현
5. Langfuse에서 검색 가능한 workflow trace를 위한 instrumentation
6. validation, 상태 전이, 응답 형태를 검증하는 기본 테스트

이 범위를 넘어가는 항목은 다음 계획으로 미룬다.
