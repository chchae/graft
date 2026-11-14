# T0 기존 실패 3건 분석 및 T1 진행 권고

작성일: 2026-11-14 (KST)  
범위: **원인 조사·반복 재현·영향 평가만 수행. 런타임 코드, 기존 테스트 및 기대값, 설정은 수정하지 않았다. T1도 시행하지 않았다.**

## 1. 결론

현재 실패 3건은 모두 변경 전 코드에서 독립적으로 재현된다. 제안하는 **미사용 import 2개 제거만의 좁은 T1**과 실패 원인은 독립적이므로, 정확한 node ID를 가진 known baseline failures로 관리하면서 해당 T1을 진행할 수 있도록 권고한다. 전체 리팩토링이나 과학 계산 경로의 변경을 무조건 허용한다는 뜻은 아니다.

| ID | 실패 | 분류 | 재현 | 판단 / 보류 범위 |
|---|---|---|---|---|
| KF-MD-01 | MD progress summary 출력 기대 | **테스트의 verbosity 전제 오류**. 최신 확정 로그 요구와 불일치 | 별도 pytest 프로세스 3/3 실패. level 2에서는 기존 기대값 그대로 통과 | 정상적인 계산·DB 진행 오류는 발견하지 못함. driver logging/progress 관련 구조 변경은 별도 테스트 정합화 전 보류 |
| KF-MD-02 | MD pending의 elapsed/current-time 출력 기대 | **테스트의 verbosity 전제 오류**. 계산된 시간의 오류가 아님 | 별도 pytest 프로세스 3/3 실패. level 2에서는 기존 기대값 그대로 통과 | 시간 저장/완료 장벽 오류는 발견하지 못함. driver timing/logging 경계의 구조 변경은 별도 정합화 전 보류 |
| KF-CHEM-01 | 두 phenyl fragment 조합의 무조건 clash 기대 | **현재 환경·계산 정책에서 fixture의 판정 기대값 오류**. spatial checker의 false negative 증거 없음 | 원래 pytest 3/3 실패. 같은 프로세스 20회 및 새 프로세스 5회 모두 같은 PASS pose | 다른 RDKit 버전까지 보증하지 않음. search/assembly/clash의 구조 변경은 더 견고한 회귀 fixture 확보 전 보류 |

이번 조사로 확인한 것은 현재 동작과 기대값의 불일치 원인이다. 기존 실패를 skip/xfail로 숨기거나 assertion을 완화하지 않았으며, 과학적 정확성 검증 전체를 완료했다는 뜻도 아니다.

## 2. 기준 환경·조사 방법

- T0 보고서: [T0_BASELINE.md](T0_BASELINE.md), [환경·원본 SHA256](t0/baseline.json), [T0 전체 테스트 로그](t0/pytest-after.log).
- 작업 트리는 HEAD `392674a481d41951e031f0ea2716db1e9f0271f9`에 미커밋·미추적 변경을 포함한 T0 snapshot과 같다. 조사 전 원본 Python 파일과 T0 설치 복사본의 checksum 일치를 확인했다.
- Python 3.15.2, RDKit 2026.09.1, NumPy 2.5.4, pytest 9.2.0. T0의 별도 설치 `/tmp/graft-t0-l0xs4f55/venv`와 소스 복사본 `/tmp/graft-t0-l0xs4f55/project`를 사용했다. 의존성을 설치·업그레이드하지 않았다.
- 진단 파일·일회용 스크립트·synthetic DB/분자는 `/tmp/graft-t0-known-c4es1rbe`에만 작성했다. 원본 테스트 코드나 runtime 함수를 바꾸지 않고, 지원되는 log level 설정과 별도 분자 복사본을 이용했다.
- 각 실패 node를 새로운 pytest 프로세스에서 **세 차례씩 단독 실행**했다. 하나의 실패가 다른 실패의 실행 순서 때문에 발생하는지 구별했다.
- MD는 level 1/2 × worker exit 0/1에서 출력, 모든 compound row, frozen selection, progress 파일, stage timer를 대조했다. worker 실행은 mock이므로 native MD는 수행하지 않았다.
- 분자는 원래 fixture·seed·최적화 조건을 유지해 반복했고, 별도의 전수 원자쌍 계산으로 spatial-index 결과를 대조했다. JobServer/Slurm/Glide/Amber/Prime/Desmond/TL 계산, 운영 DB·JobId·remote helper·production 결과는 변경하지 않았다.

### 이번 실행 결과

| 조사 | 결과 |
|---|---|
| 실패 3개 node 개별 실행, 각 3회 | 9회 모두 원래 assertion에서 실패. 새 exception/다른 실패 없음 |
| 두 MD node를 log level 2에서 실행 | **2 passed**, 기대값/테스트 파일 변경 없음 |
| logging·timing·MD progress·sequential driver·3D controls | **249 passed, 기존 3 failed**, 8.68초. 다른 실패 없음 |
| 동일 프로세스의 같은 분자 검색 20회 | 20/20 PASS, 좌표 SHA256 1종, energy 동일 |
| 새 Python 프로세스 5회 | 5/5 PASS. `PYTHONHASHSEED=0,1,17,42,123`에서도 부모·결과 좌표 각각 SHA256 1종 |
| MD level 1/2 × exit 0/1 진단 | 네 경우 모두 compound·selection·progress 파일 동일, 완료로 진행하지 않음 |

관련 controls 명령은 다음과 같다. 세 실패도 포함하여 실행했으며 제외하지 않았다.

```bash
PYTHONPATH=/tmp/graft-t0-l0xs4f55/project/src \
/tmp/graft-t0-l0xs4f55/venv/bin/python -m pytest -q -ra \
  tests/test_driver_logging.py tests/test_stage_timing.py \
  tests/test_md_progress_summary.py tests/test_sequential_driver.py \
  tests/test_combichem_3d.py
```

T0의 마지막 전체 결과는 **2,646 passed, 3 failed, 7 skipped**다. 이번에는 runtime 변경이 없으므로 전체 suite를 다시 반복하는 대신 실패별 재현 및 영향 범위 controls를 실행했다. 위 249개 통과를 전체 suite 결과와 혼동하지 않는다.

## 3. KF-MD-01 — MD progress summary 출력 기대

### 대상과 관찰

Node:

```text
tests/test_md_progress_summary.py::test_driver_pending_line_includes_progress_and_elapsed_even_on_worker_error[0]
```

테스트는 worker exit 0, 미완료 MD selection 24개, 기본 log level 1에서 `driver.supervise_iteration(..., once=True)`를 호출한다. `md-gbsa pending:`, queued/running/calculated/db_committed/db_pending 수치, `elapsed=`가 stdout에 나타날 것을 기대하지만 실제 stdout은 비어 있다. `[1]` parameter는 실패 목록에 포함되지 않으며 worker 오류 진단이 출력되어 통과한다.

### 원인과 분류

- `src/graft/driver.py::supervise_iteration`의 `routine_md_gbsa_poll`은 성공 worker의 `MD-GBSA still pending for compound ...`를 인식한다. 해당 출력에는 `print()` 대신 `progress()`를 선택한다.
- `src/graft/stages/_logging.py::progress`는 `enabled(2)`일 때만 출력한다. 기본 level은 1이다. summary를 구하지 못해서 stdout이 없는 것이 아니다.
- SPEC의 **“MD recovery polling messages only in verbose mode (2026-11-09)”**는 `Confirmed user request`로 정상 MD pending을 verbose >=2에서만 보이게 하고, worker 오류·운영 blocker는 모든 level에서 보이게 한다. 이전의 **“MD-GBSA pending progress counts (2026-11-04)”**는 출력할 때의 집계 필드를 규정하며, 최신 verbosity 요구를 되돌리는 근거가 아니다.
- 커밋 `5703bad`는 `routine_md_gbsa_poll`의 level 2 분기와 대응하는 `test_driver_logging.py` 테스트를 함께 도입했다. 현재 구현은 그 의도 및 최신 확정 요구와 일치한다.
- 반대 방향의 정상 회귀 테스트 `test_md_supervisor_pending_is_verbose_but_worker_failures_stay_visible`도 controls에서 통과한다.

**분류:** 런타임 버그나 무작위 출력 손실보다는, 출력 내용을 검사하면서 필요한 level 2를 설정하지 않은 **테스트 전제 오류**로 판단한다. test가 verbosity를 명시하지 않으므로 주변 context level에 영향을 받을 여지는 있지만, 이번 별도 프로세스 level 1 재현은 세 번 모두 동일했다.

### 영향 범위와 정상 기능 평가

현재 영향을 받는 것은 정상 pending의 **화면 표시**다. level을 바꾼 네 조합에서 다음을 확인했다.

| Log level | Worker exit | 정상 pending/오류 출력 | compound / selection / progress | driver 반환 / timer |
|---|---:|---|---|---|
| 1 | 0 | 생략 | 모두 기존 값 보존 | False / 미완료 |
| 2 | 0 | progress 집계·elapsed 출력 | 모두 기존 값 보존 | False / 미완료 |
| 1 | 1 | worker 오류·집계·elapsed 출력 | 모두 기존 값 보존 | False / 미완료 |
| 2 | 1 | worker 오류·집계·elapsed 출력 | 모두 기존 값 보존 | False / 미완료 |

진단 fixture는 `queued=4, running=6, calculated=14, db_committed=12, db_pending=2`를 유지했다. verbosity 차이가 계산 제출·점수·compound 상태·selection 변경 또는 다음 stage 진행을 일으킨 증거는 없다. 이는 mock worker/임시 DB 범위의 검증이며 실제 MD 수치 동등성 시험은 아니다.

### 별도 수정 제안

향후 **테스트 정합화만의 patch**에서 이 테스트가 summary 내용 검증이라면 level 2를 명시하도록 한다. quiet/verbose와 nonzero worker error 정책은 기존 `test_driver_logging.py`로 계속 검증한다. 기존 assertion을 삭제하거나 runtime 로그를 level 1로 되돌리는 방식은 제안하지 않는다. 이번 단계에서는 수정하지 않았다.

## 4. KF-MD-02 — MD pending elapsed/current-time 출력 기대

### 대상과 관찰

Node:

```text
tests/test_sequential_driver.py::test_pending_log_uses_persisted_iteration_stage_elapsed[md-gbsa-MD-GBSA still pending for compound 624598]
```

이 테스트는 DB stage timer를 100초에 시작하고 현재 시간을 700초로 고정한다. worker exit 0과 정상 MD pending reason을 반환하도록 mock한 뒤, **기본 level 1**의 stdout에서 `elapsed=00:10:00.0`과 고정 current_time을 찾는다. 결과는 빈 stdout이다. 같은 함수의 Amber parameter는 이 정상 MD-only 출력 분기에 들어가지 않아 통과한다.

### 원인과 분류

KF-MD-01과 같은 `report = progress ...` 분기가 원인이다. elapsed 값을 계산하거나 DB에 보존하는 코드의 실패가 아니다. 두 node를 기존 그대로 level 2에서 실행하면 모두 통과하므로, 이 테스트가 요구하는 600초 및 current_time 문자열도 그대로 나온다. 별도 timer/driver logging controls에서도 신규 실패는 없었다.

**분류:** 최신 verbosity 정책을 반영하지 않은 **테스트 전제 오류**다. 현재 환경의 clock, timezone 또는 비결정적 wall time이 원인이라는 증거는 없다. 테스트 자체가 time/current_time을 고정한다.

### 영향 범위와 별도 수정 제안

- 영향은 MD pending 메시지 표시 조건이며, compound score·분자 계산 결과와 분리된다. timer는 정상 pending에서 finish로 전환되지 않는다.
- 향후 elapsed 문구 내용 검증은 명시적 verbose level에서 하고, 기본 level의 생략 및 오류 시 표시 여부는 별도 정책 테스트로 유지한다.
- `driver.py`, `_logging.py`, stage timing·progress contract를 옮기는 리팩토링은 이 기대 조건 정합화를 먼저 처리하거나, 해당 두 known failure뿐 아니라 상세 logging controls의 outcome까지 대조해야 한다. 이번 T1의 두 import 제거는 이 코드에 손대지 않는다.

## 5. KF-CHEM-01 — 무조건 CLASH_REJECT를 기대하는 fixture

### 대상과 원래 조건

Node:

```text
tests/test_combichem_3d.py::test_real_probes_pass_but_actual_fragment_combination_collides_with_itself
```

이 fixture는 실제 EGFR binding site가 아니다. 작은 ethylbenzene parent, 원점에서 먼 단일 Ne receptor와 lineage를 생성한다. 두 frontier carbon에 각각 phenyl fragment를 붙인다. 조건은 다음과 같다.

- methyl probe와 product search seed 37.
- `conformers=1`, `torsion-trials=0`, `optimization-iterations=1000`.
- `overlap-tolerance=0.0`, `one-four-policy="exclude"`, `include-hydrogens=false`.
- 원래 benzene core는 고정되고 sidechain은 MMFF 최적화할 수 있다. torsion trial이 0이어도 embedding과 force-field optimization은 수행한다.
- 두 methyl probe가 PASS한 다음 완성 분자를 새로 검색한다. 테스트는 이 완성 분자에 `CLASH_REJECT`, 서로 다른 신규 fragment 사이의 충돌 예시, `not a proof` reason을 기대한다.

### 재현 결과와 판정 근거

같은 프로세스 20회와 새 프로세스 5회의 결과는 동일했다.

| 항목 | 관찰값 |
|---|---|
| Methyl probes | 둘 다 PASS |
| Product result | PASS, 시도 1회, valid pose 1개, errors 없음 |
| Optimization | MMFF, converged, return code 0 |
| Ligand energy | 79.90572839646777 |
| Fixed core 최대 변위 | 0.0 Å |
| Product canonical SMILES | `c1ccc(CC(c2ccccc2)c2ccccc2)cc1` — 조립한 product와 동일 |
| 신규 fragment 보존 | 서로 다른 두 fragment에 heavy atom 각각 6개 |
| 좌표 SHA256 | `b3dae5b6e279c5cf8d7d511122f3465d7c16d3e598593aac4d9ea9c25f5a0a2f` |
| BumpChecker receptor/internal clash | 0 / 0 |

**같은 checker를 다시 호출한 결과만으로 결론내리지 않았다.** 별도 진단은 SpatialIndex와 checker의 3-bond BFS를 사용하지 않고 다음 방식으로 대조했다.

1. pose의 heavy atom 좌표와 RDKit 원소별 vdW 반지름을 읽는다.
2. RDKit 전체 graph-distance matrix로 1–2, 1–3, 설정된 1–4 제외를 적용한다.
3. 나머지 **113개 원자쌍을 전수 순회**하여 `r_i + r_j - distance > 0.0`을 계산한다. receptor도 모든 ligand/receptor 쌍을 전수 대조한다.
4. 두 방식 모두 internal/receptor clash 0이다. 가장 작은 eligible 거리 여유 `distance-(r_i+r_j)`는 **+0.0556289375455119 Å**로, 겹침 경계에 걸린 floating-point 동률은 아니다.
5. 검사기의 무조건 PASS 가능성을 확인하려고 **pose 복사본에서만** bond distance 4인 두 heavy atom을 같은 좌표에 놓았다. BumpChecker와 전수 검사 모두 internal clash 8개를 검출했다. 원래 pose, parent, 코드·입력 설정에는 이 변경을 적용하지 않았다.

이 독립 대조는 거리 순회/graph 탐색 구현을 다르게 한 검증이다. 동일한 RDKit 원소 반지름 및 명시된 heavy-atom·bond exclusion 모델을 사용하므로 별도의 고정밀 물리 계산이나 친화도 검증은 아니다.

### 원인과 분류

`search.py::search()`는 embedding → MMFF 최적화 → core displacement/stereo/finite 검증 → `checker.check(candidate)`를 거친다. 유효 pose가 충돌 검사를 통과하면 PASS가 맞고, **검색한 유효 pose가 모두 충돌할 때만** CLASH_REJECT다. SPEC의 joint constrained search 및 finite-search 판정과 일치한다.

두 phenyl fragment를 도입했다는 사실만으로 그 분자의 현재 seed/최적화 결과가 반드시 겹친다는 보장은 없다. 실제로 fragment가 빠지거나 core 고정이 풀린 것이 아니라, graph·core를 보존한 현재 유효 pose가 정해진 충돌 모델을 통과했다. 작은 fixture에서 “이 geometry search는 반드시 CLASH_REJECT일 것”이라는 고정 기대가 성립하지 않는다.

**분류:** 현재 환경에서는 **과도하게 구체적인 fixture 기대값 오류**로 판단한다. 검사기가 충돌을 놓쳐 과학 결과를 잘못 통과시켰다는 근거는 얻지 못했다. 고정 seed를 둔 이번 반복에서는 비결정성도 관찰하지 못했다.

다만 다음은 미확인이다.

- Python/RDKit/force-field 구현의 다른 버전에서 같은 pose가 나오는지는 시험하지 않았다. 이 실패가 최초로 생긴 역사적 원인이 코드 변화인지 RDKit 환경 변화인지는 단정하지 않는다.
- +0.0556 Å 여유는 현재 좌표의 확정된 PASS를 뒷받침하지만, 다른 최적화 결과까지 PASS를 보장하지 않는다. 이를 cross-version 좌표 동등성 계약으로 삼지 않는다.
- H atom을 제외하고 1–4를 제외하는 현 모델을 검증한 것이며, 일반적인 분자 물리 타당성·실제 수용체 충돌·affinity를 보증하지 않는다.

### 영향 범위와 정상 기능 평가

- 해당 경로는 `assembly.py`, `search.py`, `clashes.py`, `stages/combichem_product.evaluate_product()` 및 local/remote/Slurm calculators에 공유된다. standalone fragment growth뿐 아니라 CReM product search도 같은 하위 계산을 사용한다. **의심 없이 모든 chemistry 리팩토링으로 확대할 영역은 아니다.**
- 현재 product 경로는 각 product를 다시 검색·검사한다. methyl probe PASS만 보고 실제 fragment 조합을 자동 통과시키는 shortcut은 발견하지 못했다.
- 실제 충돌 검출 controls, 고정 core, stereochemistry·실패 분류·optimization controls를 포함한 관련 묶음에서 다른 신규 실패는 없었다. 이 synthetic 사례가 저장된 docking/Amber/Prime/MD energy를 바꾸거나 덮어썼다는 증거는 없다.
- 이 한 사례를 해결하려고 overlap tolerance, vdW radius, bond exclusion, seed, optimization 조건이나 PASS 규칙을 바꾸면 **생성되는 실제 분자 집합에 영향을 줄 수 있다.** 그런 runtime 변경은 하지 않으며 별도 과학 변경으로 취급한다.

### 별도 수정 제안

단순히 `CLASH_REJECT → PASS`로 assertion 한 줄을 바꾸는 patch는 권고하지 않는다. 원래 목적은 “독립 probe PASS가 다중 fragment 호환성을 보장하지 않는다”는 것이다. 별도 테스트 수정에서 다음을 분리한다.

1. 실제로 충돌하는 것으로 전수 대조한 geometry fixture로 receptor/internal checker를 검증한다.
2. product 경로가 probe 결과와 별개로 실제 product search·checker를 호출하고 판정을 존중하는지 검증한다.
3. 최적화된 실제 fixture는 특정 seed의 불가피한 CLASH를 가정하기보다 graph·core·stereo·충돌 모델과 반환 verdict의 일관성을 검증한다.

현재 검색/최적화 정책을 바꿔 옛 expected verdict를 억지로 얻지 않는다. 이 수정은 별도 작업으로 남기며 이번 조사에서는 기존 테스트를 유지했다.

## 6. Known baseline failures 관리와 변경 보류선

### 진행 가능한 범위

세 실패를 **정확한 node ID와 원인·환경을 기록한 baseline 예외**로 관리하면, 아래의 아주 좁은 T1은 진행 가능하다고 권고한다. 이는 실패를 수정/제외한 것으로 계산하지 않는 조건이다.

- 전체 pytest는 exit 1이어도 결과를 그대로 기록한다. pass/fail 숫자만 비교하지 않고 기존 node의 outcome 및 실패 assertion/원인까지 대조한다.
- 새 실패, skip 증가, 기존 통과 case의 실패, 기존 실패가 다른 원인으로 바뀌는 현상이 생기면 중단한다.
- T1로 이 세 실패가 갑자기 통과하면 무조건 개선으로 인정하지 않는다. test order·verbosity 누출·환경·예상하지 못한 동작 변화부터 조사한다.
- 글로벌 xfail/skip marker, assertion 완화, source fingerprint 우회·manifest 재작성은 추가하지 않는다.

### 보류할 범위

- `driver.supervise_iteration`, `_logging`, pending/timer contract의 **구조 변경**: 두 MD 테스트를 최신 로그 요구와 정합화하는 별도 작업 전 보류한다.
- `assembly`, `search/optimize`, `BumpChecker`, 실제 product 계산의 **구조 변경**: checker oracle 및 견고한 다중 fragment 회귀 fixture를 먼저 확보한다. 기존 실패가 단순 fixture 문제로 분류됐더라도 그 테스트가 원래 막으려던 오류까지 검증되었다고 간주하지 않는다.
- source fingerprint를 가진 chemistry/native helper는 진행 중 작업 재개 제약까지 별도 확인한다. known failure 등록이 fingerprint 제약을 해제하지 않는다.

## 7. 다음 T1의 구체적 제안 — 아직 적용하지 않음

**하나의 논리 작업:** 현재 저장소에서 미사용으로 확인되는 `sqlite3` import 두 줄만 제거하는 작은 patch. 함수 삭제·분리·이름 변경이나 계산·SQL·로그 수정은 포함하지 않는다.

| 대상 파일 | 예정 변경 | 사용 여부 조사 | 위험 / 검증 |
|---|---|---|---|
| `src/graft/stages/generate.py` | 상단 `import sqlite3` 1줄 제거 | AST Name 참조·문자열 script 내부 참조 모두 0. 필요한 DB 처리는 현재 `_core`/`database` 경로를 사용 | 낮음. generator 선택·CLI/설정·CReM 생성/저장/복구 테스트로 동작 보존 확인 |
| `src/graft/stages/glide.py` | 상단 `import sqlite3` 1줄 제거 | AST Name 참조·문자열 script 내부 참조 모두 0. direct SQL call도 database가 제공한 connection을 사용 | 낮음. 입력·claim·group 결과 저장·UNKNOWN/JobId 복구 테스트 확인. **이미 존재하는 미커밋 Glide 변경은 그대로 보존** |

두 모듈의 `sqlite3` attribute를 사용하는 저장소 내 caller·test monkeypatch·문서상 공개 API는 찾지 못했다. 외부 미제공 스크립트의 의도하지 않은 attribute 접근까지 부재를 증명한 것은 아니다. 이런 caller가 확인되면 해당 제거를 보류한다. public function/CLI signature는 유지한다.

확인한 CReM/standalone source fingerprint 목록은 `chemistry/combichem3d/*.py`, `crem_generate.py`, `combichem_3d.py`, `combichem_product.py`, `combichem_buffer.py` 및 distributed helper다. **위 두 파일은 그 source fingerprint의 직접 대상이 아니다.** Glide에 있는 입력 checksum은 LigPrep/pose 입력의 checksum으로, 이 import 제거를 위해 건드리지 않는다. native helper나 원격 업로드 소스도 수정하지 않는다.

다음 후보는 이번 첫 T1 patch에서 제외한다: `_core.argparse`, `combichem_3d.assemble`, `combichem_slurm_pool.json`, `_deleted_md.Path`, legacy runner/API. 대형 core·의도하지 않은 re-export·문자열 helper·source fingerprint 위험을 하나의 작은 정리에 섞지 않는다.

### T1에서 실행할 검증

변경 **전후 같은 환경**에서 다음 관련 묶음을 실행한다.

```bash
python -m pytest -q \
  tests/test_generation.py tests/test_generation_sa.py tests/test_crem_pipeline.py \
  tests/test_project_config.py tests/test_refactoring_contracts.py \
  tests/test_glide_pipeline.py tests/test_glide_recovery.py \
  tests/test_glide_status_unavailable.py tests/test_stage_barriers.py
python -m graft generate --help
python -m graft glide --help
```

그 뒤 전체 `python -m pytest`, `python -m compileall -q src tests`, `python -c "import graft; import graft.cli"`를 격리된 설치에서 실행한다. T0와 같은 환경·입력에서 전체 outcome이 **2,646 passed / 기존 동일 3 failed / 동일 7 skipped**인지 대조하고, 위 세 known failure의 원래 원인도 유지되는지 확인한다. 숫자가 일치해도 다른 node가 실패하면 회귀로 판단한다.

기존 보존 테스트가 이 import 정리에 필요한 실행 경계를 이미 검사하므로, 단순히 “소스에 import가 없다”는 형태의 구현 복제 테스트를 새로 추가할 필요는 없다. 후속 조사에서 실제 API/동적 참조 경계가 드러나면 제거 전에 그 계약을 먼저 확보한다.

**최종 권고:** 위 제한된 T1만 known baseline failures를 명시하여 진행할 수 있다. MD logging과 chemistry 구조 변경은 위 보류선을 따른다. 이 문서는 진행 권고이며 T1 소스 변경 자체는 수행하지 않았다.
