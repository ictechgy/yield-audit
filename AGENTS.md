# AGENTS.md — yield-audit

AI 코딩 에이전트(ZCode, Claude Code 등)가 이 저장소에서 작업할 때의 규약입니다.
사람 기여자는 먼저 [README.md](README.md)를 읽으세요. 내용이 겹치면 이 문서가 우선합니다.
렌즈 패키지와 테스트에는 하위 규약이 있습니다: [src/yield_audit/lenses/AGENTS.md](src/yield_audit/lenses/AGENTS.md), [tests/AGENTS.md](tests/AGENTS.md).

## 이 프로젝트가 하는 일

AI 코딩 에이전트(Claude Code)의 로컬 세션 트랜스크립트와 git 이력을 대조해 **성과를 회계**합니다:
무엇이 살아남았는지(M1 생존율), 무엇이 낭비였는지(M2), 재시도 세금(M3), 채택 작업당 비용(M4),
캐시 지역성(M5), 검증 공백(M8). 원칙: **완전 로컬, 읽기 전용, 결정적, 런타임 의존성 0**
(Python ≥3.10 표준라이브러리 + git CLI), **절감 주장 금지 — 측정만**.

## 명령어

```bash
python3 -m pip install -e '.[dev]'   # 또는 uv pip install -e '.[dev]'
pytest            # ~3초, 커밋 전 반드시 통과
ruff check .      # 반드시 클린
```

스모크 실행은 **픽스처/tmp 저장소에만**:

```bash
yield-audit audit --repo <tmp fixture repo> --transcripts-dir <tmp fixtures> --now 2026-08-20T00:00:00Z --format json
```

⚠️ **절대 금지**: 실제 데이터 감사 — `--repo`/`--transcripts-dir`를 `/Users/*/.claude`나
사용자의 실제 프로젝트로 향하면 1.3GB+ 전체 스캔으로 수십 초~수 분이 걸리고 사생활을 침범합니다.
에이전트가 실데이터 스모크가 필요하면 사용자에게 먼저 물으세요.

## 구조 (데이터 흐름)

```
cli.py            argparse, 종료코드 0/2, stdout 로케일 방어
  └─ audit.py     파이프라인 오케스트레이터 — 리포트 dict 조립, 마지막에 deep_sanitize
       ├─ transcripts.py   Claude Code JSONL 어댑터 → events.Session (키 기반 방어적 파싱)
       ├─ gitdata.py       읽기 전용 git 래퍼 (스트리밍, blame=SHA 카운터, GIT_* env 제거)
       ├─ attribute.py     세션↔커밋 매칭 (high/medium 등급, 1/n 분할, 모호 플래그)
       ├─ pricing/costs    공시 요금표(USD/MTok)와 관측 usage 기반 비용
       ├─ lenses/          M1–M8 측정 렌즈 (순수 함수 — 하위 AGENTS.md 참고)
       ├─ redact.py        출력 경계: 살균·레닥션·deep_sanitize
       └─ report.py        console/json/markdown 렌더러
```

## 필수 규약 (어기다면 버그)

1. **렌즈는 순수 함수** — I/O·시계·난수 금지. `now`와 horizon은 인자로 받는다.
2. **정직성 라벨** — 모든 리포트 블록에 `measurement`가 있어야 한다:
   `observed`(기록에서 직접) / `estimate`(관측 × 공시요금) / `proxy`(명시된 대체량).
   라벨 없는 새 메트릭은 버그로 취급.
3. **출력 경계** — 트랜스크립트에서 온 모든 문자열은 `redact.py`를 거친다.
   `run_audit`의 deep_sanitize는 안전망이지 레닥션의 대체품이 아니다
   (안전망은 이스케이프 제거만 하고, 경로 치환은 필드 단계에서 한다).
4. **결정성** — 출력에 흐르는 모든 순회는 `sorted()` 먼저. 메트릭에 벽시계 금지
   (재현은 `--now`). 스키마는 `yieldaudit.report.v1`, 변경은 하위호환(additive)만.
5. **런타임 의존성 0** — 표준라이브러리 + `git` CLI만. pytest/ruff는 dev에서만.
6. **서브프로세스 규율** — list argv(셸 금지), `gitdata._clean_env`로 GIT_* 제거,
   스트림은 stderr=DEVNULL + stdout EOF 후 wait, 프로세스 수는 집계 단위로 캐싱
   (유닛당 무캐시 호출 금지).
7. **측정 전용** — 절감 개입은 로드맵(v1.x) 게이트 뒤에. 리포트·문서에 절감 수치 주장 금지.

## 테스트

- `tests/conftest.py`가 고정 날짜의 픽스처 git 저장소와 실제 스키마를 모사한 합성
  트랜스크립트를 만든다. 타임라인은 conftest 모듈 docstring이 유일한 진실원천.
- 새 렌즈/메트릭 = 렌즈 단위 테스트 + `test_e2e.py` 골든 단언 추가.
  골든 수치(13/24 생존율, $0.00643 등)는 픽스처 타임라인에서 유도된 값이므로
  픽스처 변경 시 docstring과 골든 전부를 함께 갱신.
- 악성 입력 픽스처( malformed JSONL, Infinity usage, 이스케이프 문자열)는
  `test_transcripts.py`와 `test_redact.py`에. "리포트에 이스케이프 바이트 0" 보증은
  `test_e2e.py::test_report_has_no_escape_bytes_anywhere`가 지킨다.

## 릴리스

- 버전은 두 곳을 함께: `src/yield_audit/__init__.py` + `pyproject.toml`.
- Keep-a-Changelog 형식의 CHANGELOG.md 항목 추가. CI 매트릭스: 3.10–3.14 × 3 OS.
- PyPI는 미게시 상태 — README 설치 안내는 소스 설치 기준으로 유지.
