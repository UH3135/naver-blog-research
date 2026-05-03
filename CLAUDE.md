# CLAUDE.md

이 파일은 Claude Code가 이 저장소에서 작업할 때 참고하는 지침이야.
프로젝트 전반의 코딩 컨벤션·아키텍처 규칙은 아래 AGENTS.md에 정의되어 있고, 이 파일은 Claude Code 전용 보조 지침만 다뤄.

@AGENTS.md

---

## 언어

- **영어로 사고하고, 결과는 항상 한국어로 답변한다.**
  - 내부 추론(thinking), 도구 호출 description, 변수/함수명 등은 영어로.
  - 사용자에게 보이는 최종 응답·설명·요약은 한국어로.
  - 코드 주석은 기존 파일 스타일을 따르되, 새로 추가하는 경우 영어를 기본으로.

## 패키지 / 환경

- Python 의존성 관리는 항상 `uv` 사용 (`pip install` / `python -m pip` 금지).
  - 의존성 추가: `uv add <pkg>`
  - 동기화: `uv sync`
  - 스크립트 실행: `uv run <cmd>`
- 가상환경은 `.venv/` (이미 생성됨).

## 작업 완료 전 검증

코드 변경 후 "완료" 보고 전에 아래를 실행하고 통과를 확인할 것:

```bash
make lint        # ruff check
make typecheck   # mypy
make check       # lint + typecheck 한 번에
```

테스트가 관련된 변경이라면 해당 테스트도 실행. UI/엔드포인트 변경은 `make dev`로 실제 호출까지 확인하고 보고하기.

## Git / 커밋

- 커밋 메시지에 `Co-Authored-By` 라인을 **포함하지 않는다**.
- 커밋 메시지는 한국어 명령형 (예: `feat: 인증 미들웨어 추가`, `fix: Langfuse 타임아웃 조정`).
  최근 커밋 로그를 참고해 스타일을 맞출 것.
- 사용자가 명시적으로 요청하기 전엔 커밋·푸시·PR 생성을 하지 않는다.

## 변경 시 자체 점검 (AGENTS.md 보강)

새 코드를 작성하거나 수정할 때 PR 만들기 전에 다음을 확인:

- [ ] 새 FastAPI 라우트에 rate limit 데코레이터가 있는가
- [ ] 새 LLM 호출에 Langfuse 트레이싱이 붙어 있는가
- [ ] 로그 이벤트 이름이 `lowercase_with_underscores` 인가, structlog에 f-string을 쓰지 않았는가
- [ ] 예외 로깅에 `logger.error()` 대신 `logger.exception()` 을 썼는가
- [ ] 임포트가 모두 파일 상단에 있는가
- [ ] 모든 DB / 외부 API 호출이 async 인가
- [ ] 시크릿·API 키가 코드에 하드코딩되지 않았는가

## 디렉터리 빠른 참조

- `app/api/v1/` — FastAPI 라우터
- `app/core/` — 설정, 로깅, 인증, 미들웨어
- `app/services/` — 비즈니스 로직 / 외부 통합
- `app/schemas/` — Pydantic 모델 (요청·응답·그래프 상태)
- `app/models/` — SQLModel ORM 모델
- `evals/` — LLM 출력 평가 메트릭 / 실행 스크립트
- `scripts/` — 환경 설정·배포 보조 스크립트

## 기타

- 새 기능은 먼저 `app/schemas/` 의 Pydantic 모델부터 정의하고, 라우터 → 서비스 → 모델 순으로 따라가는 것이 이 코드베이스 관습.
- 의심스러우면 추측하지 말고 기존 구현을 먼저 읽을 것 (AGENTS.md "When Making Changes" 절 참조).
