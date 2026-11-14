# T0 — 리팩토링 전 동작 보존 기준선

날짜: 2026-11-14 (KST)  
작업 범위: `REFACTORING_PLAN.md`의 **T0만**. 런타임 소스와 기존 테스트는 변경하지 않았다.

## 수행한 작업

1. HEAD `392674a481d41951e031f0ea2716db1e9f0271f9`뿐 아니라 미커밋·미추적 Python 파일까지 포함하여 현재 `src/`, `tests/`를 별도 임시 프로젝트에 복사했다. 테스트에 필요한 `examples/combichem-3d`의 소형 입력 생성 스크립트도 복사했다. 운영 `output*`, DB, 작업 manifest는 복사하거나 초기화하지 않았다.
2. `venv --system-site-packages`로 별도 가상환경을 만들고 복사본만 editable 설치했다. 의존성은 기존 환경에서 읽되 설치/업그레이드하지 않았다. `graft.__file__`이 운영 저장소가 아닌 복사본을 가리키는지 확인했다. 이는 코드·설치 경로 격리이며 컨테이너나 의존성 전체의 독립 복제는 아니다.
3. 소스 변경 전 전체 pytest를 실행하고, 정확한 실패 node ID·skip·warning과 환경을 보관했다.
4. 기존 C1–C7 테스트를 조사한 뒤, 직접적인 보존 테스트가 부족한 **설정 표현 동등성**과 **status 초기화/출력** 두 경계에만 characterization test를 추가했다.
5. 관련 테스트, 추가 후 전체 회귀, compile/import와 원본 파일 checksum을 확인했다. T1 이후 구조 변경은 시작하지 않았다.

검증 경로는 `/tmp/graft-t0-l0xs4f55/project`, 가상환경은 `/tmp/graft-t0-l0xs4f55/venv`다. 임시 복사본 보존 여부와 무관하게 환경·명령·결과·원본 파일 SHA256은 [baseline.json](t0/baseline.json)에, 원본 pytest 출력은 아래 로그에 남긴다.

## 변경 파일

- `tests/test_refactoring_contracts.py`: 아래의 두 계약 테스트만 추가.
- `tests/data/refactoring/status-existing-db.txt`: 변경 전 실제 stdout을 기록한 fixture. 임시 DB 절대 경로만 `<DB>`로 치환했다. 열, 공백, 숫자, 순서, 개행은 그대로 보존한다.
- 이 보고서와 `docs/refactoring/t0/`의 검증 증거. 기존 소스·기존 테스트·TOML·요구사항 문서는 수정하지 않았다.

### 추가 테스트가 고정하는 계약

| 테스트 | 관찰한 현재 동작 | 검증하는 회귀 |
|---|---|---|
| `test_crem_table_and_dotted_keys_load_identically_without_initializing` | 동일 파일에서 `[combichem-3d]`와 `combichem-3d.*`가 project/growth loader 모두 같은 유효 설정을 반환. cwd가 달라도 경로는 TOML 기준. 읽기만으로 DB/출력 파일을 만들지 않음 | 설정 분리 시 section/root 키 누락, 상대 경로·우선순위 변경, pure loader에 초기화 부작용 유입 |
| `test_status_backfills_legacy_reference_metadata_and_preserves_scientific_rows` | descriptor가 없는 구형 schema의 reference metadata를 보완하고 stdout을 출력. 원래 score·분자 identity·state·실패 진단과 generated row의 미계산 descriptor는 보존. 재호출은 reference descriptor를 재계산하지 않음 | status를 몰래 read-only로 바꾸는 변화, 초기화/마이그레이션 누락, 출력 포맷·정렬 변경, 기존 결과/실패 덮어쓰기 |

status 테스트는 임시 SQLite와 실제 loader/초기화/RDKit 계산을 사용한다. native command 호출은 실패하도록 막았다. 생성된 데이터는 작은 synthetic 분자이며 production 과학 결과가 아니다. 테스트 첫 작성 시 최신 schema helper가 이미 descriptor를 만드는 점을 잘못 가정한 fixture 실패 1건이 있었다. 이를 구형 schema를 직접 구성하도록 수정했으며 프로그램 소스나 기대 출력은 바꾸지 않았다.

## 검증 결과

| 실행 | 결과 |
|---|---|
| 변경 전 전체 suite | **2,644 passed, 3 failed, 7 skipped**, 330 warnings, 86.44초, exit 1 |
| 새 테스트 및 관련 5개 파일 | **241 passed**, 1.00초, exit 0 |
| 보존 테스트 추가 후 전체 suite | **2,646 passed, 3 failed, 7 skipped**, 330 warnings, 85.45초, exit 1 |
| 기존 2,654개 case의 전후 outcome 비교 | 변경 0개; 새 case 2개 모두 통과 |
| compileall / `import graft; import graft.cli` | 성공 |
| 원본 입력 무변경 검증 | 기존 Python 소스·테스트·pyproject.toml 204개 SHA256 일치 |

원본 로그: [변경 전](t0/pytest-before.log), [추가 후](t0/pytest-after.log). 시간은 이번 테스트 실행의 경과 시간이며 성능 개선 비교가 아니다.

전체 실행 명령은 다음과 같다. 각 실행은 다른 XML/로그 파일에 저장했다.

```bash
PYTHONPATH=/tmp/graft-t0-l0xs4f55/project/src \
/tmp/graft-t0-l0xs4f55/venv/bin/python -m pytest -q -ra \
  --junitxml=/tmp/graft-t0-l0xs4f55/pytest-before.xml

# 위 복사본에 새 테스트/fixture만 추가한 다음 같은 조건으로 전체 재실행
PYTHONPATH=/tmp/graft-t0-l0xs4f55/project/src \
/tmp/graft-t0-l0xs4f55/venv/bin/python -m pytest -q -ra \
  --junitxml=/tmp/graft-t0-l0xs4f55/pytest-after.xml
```

관련 테스트 묶음은 `test_refactoring_contracts.py`, `test_project_config.py`, `test_database.py`, `test_driver_logging.py`, `test_output_paths.py`다. `compileall -q src tests`와 `import graft; import graft.cli`도 격리된 복사본에서 검증했다.

## 기존 실패 원인과 처리

세 실패는 새 테스트 추가 전부터 존재한다. 새 테스트가 기대값을 바꾸거나 기존 실패를 xfail/skip으로 숨기지 않는다.

### B1. 실제 분자 self-clash 기대값 불일치

- Node: `tests/test_combichem_3d.py::test_real_probes_pass_but_actual_fragment_combination_collides_with_itself`.
- 기대: seed 37의 synthetic 분자에서 methyl probe는 PASS여도 두 phenyl fragment 조합은 `CLASH_REJECT`.
- 관찰: MMFF 최적화가 수렴하고 첫 시도에서 유효 pose를 얻어 `PASS`. 같은 pose를 `BumpChecker.check()`에 다시 전달해도 receptor/internal clash 모두 0이며 fixed core 최대 변위는 0.0 Å다. 관찰된 ligand energy는 79.90572839646777이다. 진단 수치는 `baseline.json`에 보관한다.
- 코드 경로: `chemistry/combichem3d/search.py::search/optimize`와 `clashes.py::BumpChecker.check`. 단순 mock 반환값 차이가 아니라 실제 RDKit fixture의 기대와 관찰이 다르다.
- **미해결:** fixture가 현재 최적화에서 필연적인 충돌을 보장하지 못하는지, 판정/geometry에 결함이 있는지는 별도 과학적 검토가 필요하다. 이번 관찰로 native 과학적 타당성이나 RDKit 버전이 원인이라고 단정하지 않는다. overlap·torsion·seed·판정값·기대값을 바꾸지 않았다.

### B2. 성공 worker의 MD pending 로그를 기본 verbosity에서 기대

- Node: `tests/test_md_progress_summary.py::test_driver_pending_line_includes_progress_and_elapsed_even_on_worker_error[0]`.
- Node: `tests/test_sequential_driver.py::test_pending_log_uses_persisted_iteration_stage_elapsed[md-gbsa-MD-GBSA still pending for compound 624598]`.
- 코드 원인: `driver.supervise_iteration()`은 `rc == 0`, MD 단계, `MD-GBSA still pending for compound ...`인 경우 `progress()`를 선택한다. `progress()`는 level >=2에서만 출력하므로 level 1에서 관찰 stdout은 비어 있다. 두 테스트는 level 1에서도 pending/elapsed 출력을 기대한다.
- 반대 계약: `test_driver_logging.py::test_md_supervisor_pending_is_verbose_but_worker_failures_stay_visible`는 정상 pending을 level >=2에서만 보이게 하고 worker 실패는 계속 보이게 하는 현재 규칙을 검증한다. SPEC의 `Current wall-clock time beside elapsed reports` 검증 설명에도 같은 기존 충돌이 기록되어 있다.
- 앞선 계획의 `[0/1]` 표기는 실패 node 확인 전 조사 후보였다. 이번 실행에서 `[1]`은 통과했고, 나머지 MD 로그 실패는 `test_sequential_driver.py`의 위 node였다.
- **미해결:** 어떤 로그 계약을 적용할지 기대 충돌을 정리해야 한다. 이 T0에서는 verbosity, 메시지, 테스트 assertion을 바꾸지 않았다.

## 남은 위험과 다음 작업의 조건

- T0의 기준선·두 보존 테스트 추가·검증 기록은 마련했다. **전체 테스트는 green이 아니며, T1 이후 런타임 변경으로 넘어가지 않았다.** 사용자 지시 8번에 따라 기존 실패의 처리 방침/별도 수정이 정리되기 전 다음 논리 작업을 시작하지 않는다.
- 구현 fingerprint는 그대로다. 기존 `src/` 파일·기존 Python 테스트·`pyproject.toml`의 내용이 작업 시작 SHA256과 같은지 확인했다. 저장된 native helper·JobId·DB에는 변경을 가하지 않았다.
- C1–C7의 기존 테스트를 기반으로 검증했지만 모든 조합의 동등성을 새로 입증한 것은 아니다. 향후 T2 이후의 실제 수정 경계에는 별도 characterization test가 더 필요할 수 있다. live source hash를 우회하는 테스트/호환 allowlist는 추가하지 않았다.
- 7개 skip은 Torch 부재로 LibInvent launcher 검증이 실행되지 않은 것이다. 실제 TL/sampling과 다중 호스트/GPU/JobServer/Slurm 복구의 native scientific 동등성은 미검증이다. 설치된 Schrödinger의 임시 구조 I/O 테스트는 과학 계산 제출과 구분한다.
- `multiprocessing`의 multi-threaded `fork` DeprecationWarning이 있다. 테스트 통과를 경고 해소로 해석하지 않으며 process 시작 방식을 이번 작업에서 변경하지 않았다.
- 복사본은 운영 editable installation과 분리되어 있지만 기존 의존성을 공유하므로 완전한 환경 lock 검증은 아니다. 새 기능·성능 최적화·과학 계산 조건 변경은 포함하지 않았다.
