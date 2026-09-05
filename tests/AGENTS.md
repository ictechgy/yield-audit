# AGENTS.md — tests

테스트의 유일한 진실원천은 [conftest.py](conftest.py) 모듈 docstring이다.
픽스처 타임라인과 골든 수치의 관계가 궁금하면 코드보다 그것을 먼저 읽는다.
저장소 전체 규약은 루트 [AGENTS.md](../AGENTS.md).

## 픽스처 구조

- **`build_repo()`** — 고정된 `GIT_AUTHOR_DATE`/`GIT_COMMITTER_DATE`로 만드는 결정적 git 저장소.
  C1(2026-08-01, 세션 A의 산출: app.py 10줄·notes.md 4줄·config.yaml 4줄·test_core.py 6줄),
  C2(2026-08-04, 미귀속 후속 커밋: app.py 절반 재작성·test_app.py 추가·notes.md 삭제·config.yaml 2줄 재작성).
- **`build_transcripts()`** — 실제 Claude Code JSONL 스키마(키 이름)를 모사한 합성 트랜스크립트.
  세션 A=커밋+검증+콜드콜, B=재시도 사슬, C=컴팩션 직후 콜드 — 7개 렌즈 전부가 이 3개에서 exercised된다.
- 감사 기준 시각은 `--now 2026-08-20T00:00:00Z`(conftest의 `NOW`).

## 규칙

1. **실데이터 금지** — 테스트와 에이전트 실행 모두 `/Users/*/.claude`, 실제 프로젝트 저장소를
   읽지 않는다. 픽스처만. 테스트는 오프라인에서 통과해야 한다.
2. **git 픽스처는 `_git` 헬퍼로만** — `GIT_CONFIG_GLOBAL=/dev/null` 포함(제거 금지:
   사용자 gitconfig가 테스트를 오염시킨다). 날짜 고정은 env로.
3. **E2E는 in-process** — `cli.main(argv)`를 직접 호출하고 stdout JSON을 파싱한다.
   재현성이 필요하면 항상 `--now`를 넣는다. 전체 스위트는 수 초 안에 끝나야 한다.
4. **골든 수치는 유도값** — `test_e2e.py`의 13/24, $0.00643, 0.5 등은 conftest 타임라인에서
   산술적으로 유도된다. 픽스처를 바꾸면 docstring → 관련 골든 전부를 한 커밋에서 함께 갱신한다.
5. **호스트 파일 배치** — malformed JSONL·Infinity usage·sidechain 레코드는
   `test_transcripts.py`, 적대적 문자열 살균은 `test_redact.py`, "리포트에 이스케이프 바이트 0"
   보증은 `test_e2e.py::test_report_has_no_escape_bytes_anywhere`가 담당한다. 새 안전망을
   만들면 이 구분에 맞춰 테스트를 추가한다.
6. **새 메트릭 절차** — 렌즈 단위 테스트 → 픽스처에 해당 동작을 exercise하는 요소 추가
   (conftest docstring에 문서화) → E2E 골든 단언. 이 순서를 건너뛴 메트릭은 미완성이다.
