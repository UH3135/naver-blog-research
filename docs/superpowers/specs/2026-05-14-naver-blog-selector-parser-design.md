# 네이버 블로그 Selector 기반 파서 재설계

## 개요

이 문서는 `POST /api/v1/naver-blog/search-preview` 에서 발생한 잘못된 `snippet` 추출 문제를 해결하기 위한 서버 파서 재설계 방향을 정의한다.

현재 문제는 네이버 검색 결과 페이지에서 블로그 카드 단위로 데이터를 읽지 않고, 페이지 전역에서 링크/설명/메타 텍스트를 따로 수집한 뒤 인덱스로 결합하는 방식 때문에 발생한다. 그 결과 실제 블로그 요약 대신 프로모션 문구나 카드 바깥 설명 블록이 `snippet` 으로 채택되고, 제목과 블로거명이 서로 섞이는 현상이 생긴다.

이번 설계의 목표는 정확도만 극단적으로 올리는 것이 아니라, 네이버 DOM 변화에 어느 정도 버티는 복원력 우선 파서를 만드는 것이다.

## 목표

이번 재설계는 아래를 만족해야 한다.

1. 검색 결과를 페이지 전역이 아니라 카드 단위로 파싱한다.
2. 상세 글 URL을 블로그 홈 URL보다 우선 선택한다.
3. 광고성/프로모션 문구가 `snippet` 으로 채택되지 않도록 방지한다.
4. 제목, 요약, 블로거명, 날짜를 각각 독립적으로 후보 수집 후 점수화한다.
5. 일부 필드 추출 실패가 있어도 카드 자체는 가능한 한 유지한다.
6. DOM 구조가 일부 변해도 selector 하나가 깨졌다고 전체 파서가 무너지지 않게 한다.

## 비목표

이번 단계는 아래를 포함하지 않는다.

1. Playwright 기반 런타임 스크래핑 전환
2. 광고성 결과를 완전히 의미론적으로 판별하는 ML/LLM 도입
3. 검색 결과 랭킹 자체의 고도화
4. 블로그 본문 fetch 로직 개선
5. 검색 결과 캐싱 정책 변경

## 확인된 원인

재현 결과, 문제는 응답 조립 단계가 아니라 검색 파서 단계에서 이미 발생하고 있었다.

### 현재 문제점

1. 전역 anchor 수집
   - `blog.naver.com` 링크를 카드 단위가 아니라 페이지 전역에서 수집한다.
   - 같은 카드 안의 프로필 링크, 제목 링크, 본문 미리보기 링크, 썸네일 링크가 모두 별개 결과처럼 잡힌다.

2. 전역 snippet 수집
   - `dsc_area`, `total_dsc`, `api_txt_lines` 계열 블록을 페이지 전역에서 수집한다.
   - 실제 블로그 카드 외부의 프로모션 설명 블록도 함께 매칭된다.

3. 인덱스 기반 결합
   - anchor 목록과 snippet 목록, blogger 목록을 같은 순서일 것이라고 가정하고 결합한다.
   - DOM상 서로 다른 카드 또는 카드 외부 블록이 한 결과로 섞인다.

### 재현 시 확인된 대표 오탐

실제 `snippet` 으로 아래 문구가 채택되었다.

- `좋아하는 건 누구나 남기고 싶으니까 클립 올리면 매주 쌓이는 Npay 포인트`
- `컬리N마트 5O% 첫 구매 혜택 반값 쿠폰 받고 최대 2만원 적립까지`

이 문구들은 블로그 후기 요약이 아니라 네이버 프로모션 블록 텍스트이며, 현재 파서가 카드 경계를 무시하고 전역 설명 블록을 읽고 있다는 근거다.

## 핵심 설계 원칙

복원력 우선 설계는 아래 원칙을 따른다.

1. 카드 후보는 느슨하게 수집한다.
2. 카드 내부 필드는 여러 후보를 수집한다.
3. 필드별 후보는 규칙 기반 점수로 선택한다.
4. 광고성 텍스트는 강한 필터로 제거한다.
5. `snippet` 이 비더라도 상세 글 URL과 제목이 유효하면 카드는 살린다.
6. 디버깅 가능한 점수와 선택 이유를 남긴다.

## 파서 아키텍처

엔트리포인트는 유지하되 내부 구조를 카드 중심 파이프라인으로 바꾼다.

1. HTML 로드
2. 카드 후보 수집
3. 카드별 필드 후보 수집
4. 필드 후보 점수화
5. 필드별 최고 점수 선택
6. 카드 단위 통과/탈락 판정
7. 최종 결과 후처리

권장 엔트리포인트 구조는 아래와 같다.

```python
def search_naver_blog(query: str, max_results: int = 3) -> list[dict[str, str]]:
    html = _load_search_html(query)
    return _parse_search_results(html=html, query=query, max_results=max_results)
```

## 데이터 구조

### CardCandidate

카드 후보 DOM 블록을 표현한다.

```python
@dataclass
class CardCandidate:
    card_id: str
    html_fragment: str
    text_content: str
    source_selector: str
```

### FieldCandidate

카드 내부에서 추출한 필드 후보를 표현한다.

```python
@dataclass
class FieldCandidate:
    kind: FieldKind
    value: str
    source_selector: str
    source_attr: str | None = None
    score: int = 0
    reasons: list[str] = field(default_factory=list)
```

### ParsedCard

최종 선택된 카드 결과를 표현한다.

```python
@dataclass
class ParsedCard:
    card_id: str
    url: str = ""
    title: str = ""
    snippet: str = ""
    blogger_name: str = ""
    published_at: str = ""
    card_score: int = 0
    accepted: bool = False
    rejection_reason: str = ""
    debug_notes: list[str] = field(default_factory=list)
```

## 함수 경계

### 1. 카드 후보 수집

```python
def _collect_card_candidates(html: str, query: str) -> list[CardCandidate]:
    ...
```

책임:

1. 상세 블로그 링크를 기준으로 카드 후보 컨테이너를 찾는다.
2. 여러 selector 전략으로 후보를 폭넓게 수집한다.
3. 같은 카드 후보를 중복 제거한다.
4. 페이지 전체 래퍼처럼 너무 큰 상위 컨테이너는 제외한다.

### 2. 카드 내부 후보 수집

```python
def _extract_field_candidates(
    card: CardCandidate,
    query: str,
) -> dict[FieldKind, list[FieldCandidate]]:
    ...
```

책임:

1. `url`, `title`, `snippet`, `blogger_name`, `published_at` 후보를 각각 여러 개 수집한다.
2. 아직 탈락시키지 않고 가능한 후보를 넓게 모은다.
3. selector와 속성 출처를 같이 보존한다.

### 3. 후보 점수화

```python
def _score_field_candidates(
    candidates: dict[FieldKind, list[FieldCandidate]],
    query: str,
) -> dict[FieldKind, list[FieldCandidate]]:
    ...
```

책임:

1. 각 후보에 점수와 이유를 부여한다.
2. 프로모션 문구, 홈 URL, 프로필 링크 같은 약한 후보를 감점한다.
3. 상세 글 URL, 제목형 anchor, 문장형 snippet 같은 강한 후보를 가점한다.

### 4. 카드 선택

```python
def _select_best_card_fields(
    candidates: dict[FieldKind, list[FieldCandidate]],
    query: str,
) -> ParsedCard:
    ...
```

책임:

1. 필드별 최고 점수 후보를 선택한다.
2. 카드 전체 점수를 계산한다.
3. 최소 통과 기준 미달 카드는 탈락시킨다.
4. `snippet` 만 실패한 경우 공란으로 허용할지 판단한다.

### 5. 최종 후처리

```python
def _post_filter_results(
    cards: list[ParsedCard],
    query: str,
    max_results: int,
) -> list[ParsedCard]:
    ...
```

책임:

1. 중복 URL 제거
2. 상세 글 URL 우선 유지
3. 빈 제목 카드 제거
4. 광고성 결과 재검증
5. 상위 `max_results` 개만 반환

## 카드 후보 selector 전략

selector 하나에 고정하지 않고 다단계로 후보를 모은다.

### 1차 후보: 상세 글 링크 기반

1. `blog.naver.com/<blog_id>/<post_id>` 형태 URL을 가진 anchor를 찾는다.
2. 해당 anchor의 가까운 상위 `div`, `li`, `section`, `article` 을 카드 후보로 승격한다.
3. 텍스트가 지나치게 짧거나 링크가 하나뿐인 컨테이너는 제외한다.

### 2차 후보: 제목 anchor 기반

1. 텍스트 길이가 충분한 상세 글 anchor를 찾는다.
2. 그 anchor의 상위 컨테이너를 카드 후보로 추가한다.
3. 프로필 anchor나 썸네일 전용 anchor는 제외한다.

### 3차 후보: 본문형 preview 텍스트 기반

1. 카드 내부에 20자 이상 문장형 텍스트 블록이 있는 상위 컨테이너를 후보로 추가한다.
2. 광고 키워드만 포함한 블록은 후보에서 제외한다.

### 후보 정규화

1. 동일 DOM 노드 중복 제거
2. 텍스트 길이가 비정상적으로 큰 페이지 래퍼 제거
3. 여러 카드가 섞인 공용 섹션 제거

## 필드 후보 우선순위

### URL

1. 최우선: `https://blog.naver.com/<blog_id>/<post_id>`
2. 차선: querystring 이 있어도 path 에 post id 가 있는 형태
3. 최하위: `https://blog.naver.com/<blog_id>` 홈 링크

### Title

1. 최우선: 상세 글 URL anchor 텍스트
2. 차선: 같은 anchor 내부의 headline 계열 `span`, `strong`
3. 제외: 프로필명, 본문 전체가 들어간 긴 anchor, 텍스트 없는 썸네일 anchor

### Snippet

1. 최우선: title anchor 와 같은 카드 안에서 가장 가까운 문장형 블록
2. 차선: 카드 내부 preview 텍스트 블록
3. 제외: 광고 키워드 포함 텍스트
4. 제외: 카드 외부 공용 배너/추천 텍스트

### Blogger Name

1. 최우선: 카드 메타영역의 짧은 닉네임형 텍스트
2. 제외: 도메인 문자열, 긴 문장, 제목 재등장 텍스트

### Published At

1. 날짜 패턴이 보이는 메타 텍스트 우선
2. 예: `2026. 1. 14.`, `12:40`, `3일 전`
3. 본문 문장 안 숫자는 제외

## 규칙 기반 점수 설계

필드별 후보 점수는 절대 제외 규칙과 가중치 규칙을 함께 사용한다.

### URL 점수

1. 상세 글 URL: `+8`
2. 제목 후보와 같은 anchor: `+3`
3. 블로그 홈 URL: `-4`

### Title 점수

1. 상세 글 URL anchor 텍스트: `+5`
2. 길이 8~120자: `+3`
3. 검색어 일부 포함: `+2`
4. `mark` 하이라이트 포함: `+1`
5. 프로필명처럼 너무 짧음: `-3`
6. 본문형으로 지나치게 긴 텍스트: `-4`
7. 광고 키워드 포함: `-8`

### Snippet 점수

1. title 근처 카드 내부 텍스트: `+4`
2. 길이 20~300자: `+3`
3. 문장형 조사/서술 포함: `+2`
4. 음식/장소/검색어 관련 단어 포함: `+2`
5. 광고 키워드 포함: `-10`
6. 슬로건형 짧은 문구: `-5`
7. title 과 거의 동일: `-2`

### Blogger Name 점수

1. 길이 2~30자 닉네임형: `+3`
2. 메타영역 위치: `+2`
3. 도메인 문자열 포함: `-5`
4. 문장형 텍스트: `-4`

### Published At 점수

1. 날짜 패턴 일치: `+4`
2. 메타영역 위치: `+1`
3. 본문형 긴 문장 내부 숫자: `-3`

## 광고성 텍스트 필터

광고성 문구는 별도 함수로 강하게 판정한다.

```python
def _is_promotional_text(text: str) -> bool:
    ...
```

우선 감지 대상 예시는 아래와 같다.

- `Npay`
- `포인트`
- `쿠폰`
- `적립`
- `첫 구매`
- `스토어`
- `브라우저`
- `웨일`
- `클립`

정책:

1. 키워드 1개는 약한 감점
2. 키워드 2개 이상 또는 CTA 패턴 포함 시 강한 탈락
3. `snippet` 이 광고 판정이면 카드 전체를 버리거나, 최소한 `snippet=""` 로 강등한다

## 카드 통과 정책

카드 단위 최종 통과 판단은 복원력 우선 기준으로 아래처럼 둔다.

1. 상세 글 URL이 없으면 탈락
2. title 점수가 기준 이하이면 탈락
3. `snippet` 이 광고 판정이면 공란 처리 또는 카드 탈락
4. `blogger_name`, `published_at` 는 비어 있어도 허용
5. `snippet` 이 비어 있어도 `url` 과 `title` 이 충분히 강하면 통과

예시 카드 점수:

```python
card_score = url_score + title_score + max(snippet_score, 0) + meta_bonus
```

예시 통과 최소값:

```python
MIN_CARD_SCORE = 10
```

## Fallback 정책

복원력 우선 파서는 부분 실패를 허용한다.

1. 카드 selector A 실패 시 selector B, C 로 재수집
2. `snippet` 실패 시 빈 문자열 허용
3. `blogger_name`, `published_at` 실패 시 빈 문자열 허용
4. 상세 글 URL이 없는 카드만 남으면 결과에서 제외

## 테스트 전략

### 단위 테스트

1. `_is_blog_post_url()`
2. `_is_blog_home_url()`
3. `_is_promotional_text()`
4. 각 `score_*_candidate()` 함수

### 파서 회귀 테스트

고정 fixture 또는 최소 HTML 샘플로 아래를 검증한다.

1. 광고 문구가 `snippet` 으로 채택되지 않아야 한다.
2. 상세 글 URL이 홈 URL보다 우선되어야 한다.
3. 제목과 블로거명이 서로 섞이면 안 된다.
4. `snippet=""` 는 허용되지만 광고 `snippet` 채택은 실패다.

### 재현 기반 검증

쿼리 `중구 을지면옥 블로그` 에 대해 아래 문자열이 최종 `snippet` 에 포함되면 실패다.

- `Npay`
- `컬리N마트`
- `웨일`

## 로깅과 디버깅

구조화 로그는 아래 이벤트를 권장한다.

1. `naver_blog_card_candidates_collected`
2. `naver_blog_card_parsed`
3. `naver_blog_field_selected`
4. `naver_blog_card_rejected`
5. `naver_blog_promotional_text_detected`

각 로그는 selector, score, rejection reason 을 kwargs 로 남겨야 한다.

## 점진적 적용 전략

안전한 적용을 위해 한 번에 기존 파서를 제거하지 않는다.

### 1단계

1. 새 카드 기반 파서를 내부 함수로 추가한다.
2. 기존 파서와 병렬 비교 로그만 남긴다.

### 2단계

1. 새 파서 결과가 유효하면 우선 사용한다.
2. 실패 시 기존 파서 fallback 을 유지한다.

### 3단계

1. 충분히 안정화되면 기존 전역 regex 결합 방식을 제거한다.

## 구현 순서

1. URL/광고/검색어 헬퍼 함수 추가
2. `CardCandidate`, `FieldCandidate`, `ParsedCard` 구조 추가
3. 카드 후보 수집 구현
4. 카드 내부 필드 후보 수집 구현
5. 필드 점수화 및 선택 구현
6. 후처리와 fallback 연결
7. 회귀 테스트와 로깅 추가

## 결정 요약

이번 수정은 selector 하나에 강하게 결합하는 방식이 아니라, 카드 후보를 느슨하게 모으고 카드 내부 필드를 규칙 기반으로 선택하는 복원력 우선 파서로 간다.

그 결과 기대하는 변화는 아래와 같다.

1. 광고성 문구의 `snippet` 오염 감소
2. 홈 URL보다 상세 글 URL 우선
3. 제목, 블로거명, 본문 preview 의 섞임 감소
4. DOM 일부 변경 시 전체 파서 붕괴 위험 완화
