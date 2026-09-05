# AGENTS.md — lenses 패키지

각 모듈은 하나의 측정 렌즈다: 정규화된 이벤트(`../events.py`)와 git 사실을 입력받아
dataclass를 반환하는 **순수 함수**. 리포트 JSON 조립은 `../audit.py`의 몫이므로 여기서 하지 않는다.
저장소 전체 규약은 루트 [AGENTS.md](../../AGENTS.md) — 아래는 렌즈에 특화된 규약.

## 렌즈 계약

1. **순수성**: I/O·시계·난수·환경변수 접근 금지. `now`, horizon, 요금표는 전부 인자로 받는다.
2. **반환형**: dataclass 반환 (dict 아님). report 키 이름은 audit.py가 결정한다.
3. **0 나눔**: 분모 0은 0을 지어내지 말고 `None`으로 보고한다. 모든 나눗셈에 가드.
4. **share 가중**: 생존 유닛을 합산하는 모든 집계는 `unit.attributed_added`(share 가중)를
   쓴다. 원본 `added`를 쓰면 경쟁 커밋이 청구인 수만큼 이중 계수된다.
   원본 added/survived가 허용되는 곳은 유닛 자체의 소실 비율 계산뿐이다.
5. **pending 의미론**: horizon 미도래 유닛은 `pending_horizons`에 남기고 헤드라인
   rate·bounds에서 제외한다. pending은 "측정 불가"이지 "실패"가 아니다 — 카운트만
   정직하게 보고한다. 경쟁 커밋의 pending은 (commit, path) 기준으로 셈한다.
6. **임계값**: 상수로 추출해 근거를 docstring으로 남긴다 (`REWRITE_THRESHOLD = 0.5` 등).
   인라인 매직 넘버 금지.
7. **완료 정의**: 렌즈 단위 테스트 + `tests/test_e2e.py` 골든 단언 없이는 미완성.
   M4/M8처럼 다른 렌즈 출력과 합성되는 값은 상관 경로도 함께 커버.

## 현재 렌즈와 핵심 규칙

| 모듈 | 렌즈 | 깨기 쉬운 지점 |
|---|---|---|
| `survival.py` | M1 생존율 (blame 스냅샷, 유형별 분할) | blame 카운터는 `blame_sha_counts` 캐시로만 접근; `_tree`로 파일 존재 일괄 확인 |
| `waste.py` | M2 낭비 비용 상하한 | 분모는 같은 horizon에서 분류된 유닛 합 — 헤드라인 집계 재사용 금지 |
| `retry.py` | M3 재시도 세금 | 성공 반복은 과세 불가; 과세 구간 [첫 시도, 마지막 시도] 폐구간 포함 |
| `accepted.py` | M4 채택당 비용 | 상태 4종(accepted/rejected/pending/no_output) — 커밋 있으나 미측정은 pending |
| `cache_locality.py` | M5 캐시 지역성 | 첫 콜은 정상 콜드; 경계 tie는 `<=`; compaction은 wasted에서 제외 |
| `verify_gap.py` | M8 검증 공백 | gap_rate(미검증)와 gap_rate_strict(커밋 전 미검증) 두 가지 유지 |

새 렌즈 추가 시 이 표의 한 줄과 루트 AGENTS.md의 measurement 라벨을 함께 업데이트한다.
