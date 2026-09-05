# yield-audit (한국어)

> [English README](README.md) | 한국어 문서입니다.

**yield-audit**은 AI 코딩 에이전트의 세션이 만들어낸 출력의 **운명**을 측정하는 완전 로컬 옵저버토리입니다. ccusage가 *청구서*를 보여준다면, yield-audit은 **그 토큰 중 얼마나 살아남은 코드에 쓰였는지**를 보여줍니다.

핵심 질문은 단순합니다: *에이전트가 쓴 코드 중 일주일 뒤에도 남아 있는 것은 몇 %인가? 그리고 죽은 코드에 돈을 얼마나 썼는가?*

## 빠른 시작

```bash
# 설치 (PyPI)
python3 -m pip install yield-audit   # or: uv tool install yield-audit

# 저장소 결과 보기 (기본: 설치된 모든 에이전트 트랜스크립트를 자동 스캔 —
#   Claude Code ~/.claude/projects, Codex CLI ~/.codex/sessions)
yield-audit audit --repo /path/to/your/repo

# 특정 에이전트만 보기
yield-audit audit --repo . --agent codex

# JSON / 마크다운 리포트
yield-audit audit --repo . --format json --details
yield-audit audit --repo . --format markdown > yield-report.md

# AIDD 전환 비교: 전환일을 기준으로 전/후 두 기간의 코호트 비교
yield-audit aidd --repo . --split 2026-03-01 --days 90

# 환경 점검 (git, 트랜스크립트, 세션 탐지)
yield-audit doctor --repo /path/to/your/repo
```

요구 사항: Python ≥ 3.10, git. 런타임 의존성 없음. 네트워크 호출 없음.

## 시간 창 (상호작용)

| 플래그 | 제어 대상 | 기본값 |
|---|---|---|
| `--days` | 세션·커밋 공용 창. `probable` 코호트는 이 창 안의 에이전트 세션 조인으로만 성립 | 30 |
| `--horizons` | M1 생존 스냅샷 horizon(커밋 이후 일수) | `7,30` |
| `--rework-days` | M11 리워크 horizon(커밋당) | 14 |
| `--proximity-hours` | 세션↔커밋 어트리뷰션 시간창 | 24 |

반복 감사는 `~/.cache/yield-audit`의 blame/tree 결과를 재사용합니다(git SHA로
내용 주소화 — 결과를 바꿀 수 없고 도달 속도만 바꿉니다. `--no-cache`로 비활성,
`YIELD_AUDIT_CACHE_DIR`로 위치 변경).

## 측정 렌즈 (v0.1–v0.3)

| 렌즈 | 질문 | 성격 |
|---|---|---|
| **M1 출력 생존율** | 커밋된 줄 중 horizon(기본 7일) 시점에도 그대로 남아 있는 비율. source/test/docs/config 유형별 분할 | git history 기반 측정 |
| **M2 낭비 비용** | 죽은 출력에 들인 비용. 완전삭제(하한) ~ 50%+ 소실(상한) **구간**으로 보고 | 추정(구간) |
| **M3 재시도 세금** | 실패 후 같은 명령을 반복한 실패 사슬에 소비된 토큰 비율 | 트랜스크립트 관측 |
| **M4 채택 작업당 비용** | 생존율 ≥ 50%인 세션 1건당 완전부담 비용. accepted/rejected/pending/no_output 분류 | 추정(관측 토큰 × 공시요금) |
| **M5 캐시 지역성** | TTL 만료·프리픽스 파손으로 정가를 치른 콜드 호출 수와, 캐시였다면 아꼈을 금액. 컴팩션 직후 재구축은 예외 분류 | 추정(관측 토큰 × 공시요금) |
| **M8 검증 공백률** | 커밋 전 검증 명령이 없던 세션 비율(미검증율·엄격율 두 가지) + 검증 유무별 생존율 상관 | 트랜스크립트 관측 |
| **M11 AI 리워크율** | AI 표지 커밋은 인간 커밋보다 rework horizon(기본 14일, `--rework-days`) 내 얼마나 더 재작성되나. 코호트 근거(certain=푸터/probable=세션 조인/human) 분포 동봉 — 판정 도구가 아닌 계량 도구 | git history 기반 측정 |

### 정직성 계약

- 모든 수치에 `measurement` 라벨이 붙습니다: `observed`(트랜스크립트/git에서 직접 관측) / `estimate`(관측값 × 공시요금) / `proxy`(명시된 대체량 — 예: 커밋별 토큰 배분의 줄 점유 프록시).
- 어트리뷰션(세션↔커밋 매칭)은 확률적이므로 **신뢰도 등급**(`high` = 세션이 직접 커밋 실행 / `medium` = 파일·시간 교집합)과 모호 커밋 분할을 항상 보고합니다.
- "수정 = 낭비"가 아닙니다: 50% 미만 소실은 반복(iteration)으로 분류해 어느 쪽 구간에도 넣지 않습니다.
- 절감을 주장하지 않습니다. 측정만 합니다. 개입 기능은 로드맵(v1.x)에 게이트 뒤에 있습니다.

## 프라이버시

- 트랜스크립트와 git 이력을 **읽기만** 합니다. 어떤 데이터도 기기를 떠나지 않습니다(네트워크 호출 코드가 아예 없습니다).
- 리포트의 파일 경로는 기본적으로 basename으로 레닥션되고, 실패 사슬 명령어 안의 절대경로도 `<path>`로 치환됩니다(`--show-paths`로 해제).
- 트랜스크립트에서 온 모든 문자열(세션 ID 포함)은 리포트로 가기 전에 ANSI·C0/C1 제어문자가 제거되고, 리포트 전체에 재귀 살균이 한 번 더 적용됩니다 — 콘솔·마크다운 어디로 봐도 터미널을 건드리지 않습니다.
- 실패 사슬 명령어 안의 절대경로·`~/` 경로는 `<path>`로 치환됩니다. Windows UNC 경로(`\\host\share`)는 자유 텍스트에서는 미처리 한계가 있습니다.
- git 서브프로세스는 `GIT_*` 환경변수를 떼고 실행되어, 셸의 `GIT_DIR` 등이 감사 대상을 몰래 바꾸지 못합니다.
- 세션 ID는 리포트에서 앞 8자만 사용합니다.

## 방법론과 한계

- **생존 판정**: `git blame --porcelain`으로 horizon 시점 스냅샷에서 커밋이 추가한 줄의 귀속을 확인합니다. 나중 줄이 고쳐졌다면 생존하지 않은 것입니다. **v0.1은 rename/copy를 따라가지 않으므로** 이름 바뀐 파일은 삭제로 집계됩니다 (문서화된 한계).
- **커밋별 토큰 배분**: 트랜스크립트에는 커밋별 토큰이 없으므로 세션 비용을 줄 점유로 나눕니다(프록시 라벨).
- **커밋 어트리뷰션**: 세션이 편집한 파일 × 커밋 파일 집합 × 시간 근접(기본 24시간, `--proximity-hours`). 페어 프로그래밍·수동 커밋은 등급이 낮아지거나 미귀속됩니다. 여러 세션이 한 커밋을 다투면 어트리뷰션 share로 균등 분할되고 모호 커밋으로 표시됩니다.
- **스케일**: 생존 판정은 커밋×파일×horizon마다 blame을 읽으므로 비용이 커밋 수에 선형입니다. 수백 커밋 규모에서 수 초~수십 초를 예상하세요.
- **요금표**: 2026-09 공시가 내장(`pricing.py`, Anthropic + OpenAI 표준 티어). `--pricing-file`로 덮어쓸 수 있고, 미지 모델은 보수적 상위 요금 + 플래그 처리됩니다.
- 상관분석(M8)은 관찰 결과이며 인과가 아닙니다. 세션 수가 적으면 아무것도 증명하지 않습니다.

## 로드맵

- ~~**v0.2**~~ — ✅ 출시: 벤더 어댑터 패키지(Claude Code + Codex CLI, `--agent`), 세션 id 네임스페이싱. Gemini는 스키마 확보 후 추가.
- **v0.3** — ✅ M11 AI 리워크율 출시(코호트 certain/probable/human, `--rework-days`). 남은 항목: M12 정착률(blame 스냅샷), M13/M14(CI 데이터 의존)
- **v0.4** — ✅ `aidd` 전환 비교 리포트 출시(전환일 `--split` 기준 전/후 두 기간, 코호트 비교). 영구 캐시·Codex 트랜스크립트 프루닝 포함
- **v1.x** — 개입 계층(재시도 조기 포기 훅, 결정적 오라클 라우팅) — 각자 증거 게이트 뒤에서

## 개발

```bash
git clone <this repo> && cd yield-audit
python3 -m pip install -e '.[dev]'   # or: uv pip install -e '.[dev]'
pytest                               # 테스트 (고정 날짜 픽스처 git 저장소 사용)
ruff check .                         # 린트
```

기여 시: 모든 렌즈 로직은 순수 함수여야 하고(렌즈 = 이벤트 모델의 함수), 새 메트릭에는 `measurement` 라벨과 골든 테스트가 필요합니다. AI 에이전트로 기여한다면 [AGENTS.md](AGENTS.md)의 규약이 이 문서의 일반 안내보다 우선합니다.

## 라이선스

Apache-2.0. 방법론적 뿌리: [arXiv:2601.16809](https://arxiv.org/abs/2601.16809)(AI 생성 코드의 생존 분석)와 "성공 1회당 완전부담비용" 관점.
