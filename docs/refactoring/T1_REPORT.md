# T1 — 미사용 import 조사 및 최소 정리

작성일: 2026-11-14 (KST)  
범위: `REFACTORING_PLAN.md`의 **T1만**. Legacy API와 테스트 기대값은 보존하고, T2는 수행하지 않았다.

## 1. 결과 요약

- 여섯 후보를 직접 사용, attribute/re-export, monkeypatch, 동적 접근, CLI, native/remote helper, 외부 caller, source fingerprint의 관점에서 조사했다.
- 실제로 제거한 대상은 `_core.argparse`, `stages.generate.sqlite3`, `stages.glide.sqlite3`, `stages._deleted_md.Path`의 **최상위 import 네 줄만**이다. 다른 import의 순서·함수·타입·로직·출력·계산 조건을 변경하지 않는다.
- `stages.combichem_slurm_pool.json`과 `stages.combichem_3d.assemble`은 코드상 미사용 후보여도 **파일 자체가 fingerprint 대상**이므로 유지한다.
- `_core.run_iteration()`과 `select_global_elites()`는 사용 불확실 legacy API로 기록하고, 함수 본문·이름·시그니처를 그대로 보존한다.
- 기존 self-clash 1건·MD pending 로그 2건은 [T0_KNOWN_FAILURES.md](T0_KNOWN_FAILURES.md)의 known baseline failures로 관리한다. 기존 테스트·assertion·xfail/skip 설정은 수정하지 않는다.

**완료:** 관련 테스트 전후 361개 통과. 전체 전후 **2,646 passed / 동일한 기존 3 failed / 7 skipped**, 신규 회귀 **0건**. 테스트 2,656개 case의 outcome은 T0와 동일하다. [검증 기록](t1/verification.json), [변경 전 로그](t1/pytest-before.log), [변경 후 로그](t1/pytest-after.log)를 보관했다.

## 2. 조사 범위와 근거

기준은 HEAD `392674a481d41951e031f0ea2716db1e9f0271f9`뿐 아니라 기존 미커밋·미추적 파일까지 포함한 현재 작업 트리다. 원본의 `src/`, `tests/`, 문서, 프로젝트 설정/스크립트를 별도 복사본으로 만들고 269개 파일의 작업 시작 SHA256 및 기존 git diff를 보관했다. 운영 `output*`, DB, native manifest/결과는 변경하지 않는다.

`src/`, `tests/`, 제공된 `examples/`의 Python·shell·job·run 스크립트, 문서·entry point를 조사했다. `tmp/a.py`, `tmp/b.py`도 legacy/candidate 참조를 확인했다. 출력 디렉터리의 과거 업로드 코드는 새 caller로 실행하거나 변경하지 않는다. 저장소 밖의 미제공 개인 스크립트·제3자 사용자 전체에 대한 사용 부재는 입증할 수 없으므로, legacy API는 제거하지 않는다.

정적 검색 한 가지로 미사용을 판정하지 않았다. 다음 증거를 함께 사용했다.

1. AST의 실제 Name load, import alias를 해석한 attribute 접근, 직접 symbol import를 조사했다.
2. 문자열에 담긴 실행 가능한 Python도 파싱했다. `_deleted_md.PROBE`의 `Path` 사용은 바깥 import의 사용이 아니라 **문자열 내부의 자체 import**에 결합되어 있다.
3. `getattr/setattr`, mock/monkeypatch, `globals/vars/__dict__`, `importlib`, 파일 기반 동적 import와 entry point 경로를 검토했다. 후보 모듈을 대상으로 속성 이름을 변수로 주는 monkeypatch 두 곳도 확인했으며, 둘 다 REINVENT 실행 함수를 교체하는 목록으로 이번 import와 무관했다 (`test_combichem.py`, `test_crem_pipeline.py`).
4. public function 시그니처와 모듈 내 문자열/내장 helper 내용을 전후 대조했다. 원래 함수 본문 AST와 나머지 module AST도 동일한지 확인했다.
5. CLI 및 stage module help, 관련 테스트와 전체 테스트를 같은 격리 환경에서 전후 실행했다.
6. source fingerprint의 실제 입력 목록과 생성 함수를 확인하고, CReM `_fingerprint()`와 Slurm `signature()`를 synthetic 파일에 대해 전후 호출했다. 운영 DB나 manifest를 검사 우회·수정하지 않았다.

**외부 인터페이스의 범위:** 제거할 네 이름은 import 과정에서 우연히 module namespace에 노출된 표준 라이브러리 binding이다. 이에 대한 문서화된 공개 API·저장소 caller·테스트 patch 계약은 발견하지 못했다. 문서화된 함수/CLI, 검증된 re-export/patch 경계는 유지한다. 임의의 외부 코드가 내부 `module.sqlite3` 등의 binding에 의존하지 않는다는 전역적 보증은 하지 않으며, 그런 caller가 발견되면 해당 제거를 보류/복구해야 한다.

## 3. 후보별 조사와 판단

### 3.1 `_core.argparse` — 제거

- **내부 직접 사용:** `_core.py`의 최상위 `import argparse` 외 Name load 0개. 해당 이름을 사용하는 내장 실행 script도 없다.
- **다른 모듈 attribute/공개 인터페이스:** `_core.argparse`, alias를 통한 같은 attribute 접근, 해당 symbol의 직접 import/re-export 계약을 찾지 못했다. 기존 `_core.sqlite3` monkeypatch는 실제로 사용되므로 그대로 유지한다. 두 이름을 혼동하지 않는다.
- **테스트 patch:** 후보 `argparse`를 patch하는 테스트가 없다. `_core.run_command`, sampling/Prime 함수 등을 patch하는 지점은 변경하지 않는다.
- **동적 접근:** 후보를 조회하는 `getattr/globals/eval` 경로는 찾지 못했다. 변수 기반 native 함수 patch 목록에도 `argparse`는 없다.
- **CLI/module entry:** package CLI·driver·각 stage parser는 자신의 `argparse`를 import한다. `_core`가 parser의 `argparse` 공급자로 사용되지 않는다.
- **Native/remote:** `_core`의 helper 문자열과 helper 생성 함수 내용은 동일하다. `argparse`는 helper에 주입되지 않으며 해당 import 제거로 새 Graft dependency를 만들지 않는다.
- **외부 caller:** 제공된 외부 실행 예제·문서에서 지원 인터페이스 증거가 없다. 실제 사용되는 `_core` 함수와 legacy 함수는 모두 보존한다.
- **Fingerprint/재개:** 조사한 저장된 source fingerprint 및 helper checksum의 파일 목록에 `_core.py`가 없다. 생성 helper 문자열/명령을 바꾸지 않으며 실제 fingerprint 전후 비교도 동일하다.

### 3.2 `stages.generate.sqlite3` — 제거

- **직접 사용:** AST Name load 및 내장 Python 문자열 참조 모두 0개. SQL 실행은 `_core`/`database`가 제공한 connection을 사용한다.
- **Attribute/re-export:** `generate.sqlite3`·직접 symbol import·관련 공개 API 증거 없음.
- **테스트 patch:** 생성 함수·descriptor/filter·workspace의 patch는 있으나 이 binding을 patch하는 지점은 없다. 표준 `sqlite3.connect`나 `_core.sqlite3.connect`의 patch는 다른 binding이므로 유지된다.
- **동적 접근:** 후보 이름을 runtime에 읽는 경로 및 후보에 대한 동적 patch 없음.
- **CLI/module:** `graft generate`, `python -m graft.stages.generate`의 parser/dispatch·signature를 유지한다.
- **Native/remote:** generator의 `run_command`/remote adapter/실제 계산 호출 및 helper payload를 변경하지 않는다.
- **외부 caller:** 제공된 Python/shell 예제에서 해당 module attribute를 사용하지 않는다. 표준 라이브러리 import binding을 제외한 실행 API는 그대로다.
- **Fingerprint/재개:** CReM/standalone/Slurm의 source fingerprint 대상에 이 파일은 없다. 경로가 비슷한 `crem_generate.py`는 fingerprint 대상이므로 수정하지 않는다.

### 3.3 `stages.glide.sqlite3` — 제거

- **직접 사용:** AST Name load·내장 Python 문자열 참조 0개. stage의 connection 사용과 실제 DB 처리는 그대로 둔다.
- **Attribute/re-export/patch:** 후보 binding의 caller·직접 import·mock 계약을 찾지 못했다. `group_status`, JobServer·DB·명령의 기존 patch는 그대로다.
- **동적 접근:** 후보에 대한 동적 lookup/patch 없음. runtime `getattr(args, ...)`는 설정 접근으로 이 module binding과 무관하다.
- **CLI/module:** `graft glide`, `python -m graft.stages.glide`의 옵션·출력·function signature는 유지한다.
- **Native/remote:** `_core`의 extractor/merger, JobId recovery, collection/submission fencing, native command·payload는 변경하지 않는다.
- **외부 caller:** 문서·예제에서 해당 표준 라이브러리 binding을 interface로 사용하는 증거 없음. CLI/worker 함수는 보존한다.
- **Fingerprint/재개:** 이 파일의 LigPrep/pose checksum은 **입력 데이터 checksum**이다. 이번 import 삭제는 입력을 바꾸지 않는다. 조사한 implementation source 목록에도 이 stage 파일은 포함되지 않는다.
- **사용자 변경 보호:** 이 파일에는 이미 미커밋 Glide 복구 변경이 있다. 이를 수정·되돌리지 않고 T1은 해당 import 한 줄만 삭제한다.

### 3.4 `stages.combichem_slurm_pool.json` — 유지

- **직접 사용:** 최상위 `json`의 AST load·내장 실행 문자열 참조 0개. `calculate()`는 `combichem_compute.serve()`를 사용하며 JSON 처리는 그 모듈이 소유한다.
- **Attribute/re-export/patch:** 후보 attribute·직접 import·mock 사용은 발견하지 못했다.
- **동적 접근:** 코드와 dynamic-call 조사에서 후보 `json` lookup 없음. runtime `import`로 `serve`, `atomic_save`, `versions`를 불러오는 경계는 별도다.
- **Entry point/native/remote:** Slurm에서 `python -m graft.stages.combichem_slurm_pool`로 실행하는 실제 calculator pool entry point다. helper 실행·배포/재개 계약과 연결된다.
- **외부 caller:** 미제공 cluster script의 import 여부까지 확정할 수 없다.
- **Fingerprint:** `combichem_slurm.signature()`의 `stages/combichem*.py` glob에 포함되고, `combichem_remote.dispatch()`의 Slurm helper hash 목록에도 명시되어 있다. 한 줄 삭제도 digest가 달라진다.
- **판단:** 미사용 후보이지만 **기존 미완료 Slurm 작업의 fingerprint 호환성 보존 조건을 충족하지 못하므로 그대로 유지**한다. 새 allowlist나 manifest 보정은 추가하지 않는다.

### 3.5 `stages.combichem_3d.assemble` — 유지

- **직접 사용:** stage에 import된 `assemble` Name load 0개. 실제 product는 `stages.combichem_product`가 `chemistry.combichem3d.assembly.assemble`을 직접 import하여 사용한다. 실제 assembler를 미사용으로 분류하지 않는다.
- **Attribute/re-export/patch:** 이 stage의 `assemble` binding을 사용하는 caller/mock은 발견하지 못했다. `stage.search`, `stage.methyl_probe`, worker 함수 등을 patch하는 기존 경계와 구분했다.
- **동적 접근:** 해당 import에 대한 lookup은 찾지 못했다. backend의 module-level worker 교체는 실제 존재하지만 이 후보 정리에 포함하지 않는다.
- **CLI/module/native/remote:** `graft combichem-3d`, 해당 stage의 module entry, local/SSH/Slurm 공용 coordination 경로를 가진다.
- **외부 caller:** 의도치 않은 re-export를 사용하는 개인 스크립트 가능성도 완전히 배제하지 않는다.
- **Fingerprint:** standalone `prepare_project()`가 자기 파일 SHA256을 저장하고 CReM `_fingerprint()`도 이 파일을 포함한다. Slurm/remote source hash에도 포함된다.
- **판단:** **기존 작업의 source fingerprint가 달라지므로 유지**한다. 과학 계산 모듈·원래 assembler·export binding을 이번 T1에서 바꾸지 않는다.

### 3.6 `stages._deleted_md.Path` — 바깥 import만 제거

- **직접 사용:** Python 모듈 최상위 `Path`의 Name load 0개. `reconcile()`은 string/dict 값과 `md_gbsa` adapter를 사용한다.
- **다른 모듈/patch:** 외부 module attribute `_deleted_md.Path` caller는 발견하지 못했다. 테스트가 patch하는 `_deleted_md.reconcile` 및 실행하는 `_deleted_md.PROBE`는 그대로다.
- **동적 접근:** `spec_from_file_location()`와 `sys.path` 조작이 있는 코드는 `PROBE` 내부다. 바깥 module의 `Path`를 조회하는 구조가 아니다.
- **Entry point:** 이 모듈 자체는 CLI parser가 아니다. deleted CReM recovery가 `reconcile()`를 호출한다. 호출 시그니처와 로직을 유지한다.
- **Native/remote:** `PROBE`는 `from pathlib import Path`를 **문자열 내부에서 자체 import**한 뒤 원격 suite Python에서 실행된다. 이 내부 import는 삭제하지 않는다. `PROBE` 바이트·compile 가능 여부, `reconcile()` 내용, standalone `exec(PROBE, {})`를 수행하는 기존 테스트를 확인한다.
- **외부 caller:** 제공된 recovery script는 `reconcile`/`PROBE` 계약을 사용하며 outer `Path`는 지원 API로 사용하지 않는다. 미제공 사용자 코드의 내부 binding 의존은 미확인이다.
- **Fingerprint/재개:** 조사한 source fingerprint는 이 local recovery 모듈을 포함하지 않는다. 원격에 전달되는 PROBE 문자열의 SHA256은 동일하며 native helper 파일/업로드 내용은 변경하지 않는다.

## 4. Source fingerprint 영향

다음은 단순히 “import는 동작을 바꾸지 않는다”는 가정이 아니라 실제 code path와 checksum을 대조한 결과다.

| Fingerprint/배포 경계 | 조사 및 전후 검증 |
|---|---|
| CReM `crem_generate._fingerprint` | `chemistry/combichem3d/*.py`, `crem_generate.py`, `combichem_3d.py`, `combichem_product.py`, `combichem_buffer.py`가 대상. 변경 네 파일은 제외. 동일 synthetic 입력으로 함수 호출 결과 동일 |
| standalone `combichem_3d.prepare_project` | 자기 stage와 chemistry source·buffer/product hash를 저장. 이 source 파일들은 모두 원본 SHA256 유지 |
| Slurm `combichem_slurm.signature` | `combichem*.py`, chemistry 및 config/database/control source 대상. 실제 함수 호출 결과 동일. pool의 `json`은 이 때문에 유지 |
| `combichem_remote.dispatch` | SSH/Slurm adapter source hash 목록을 확인. 네 변경 파일은 목록 밖이며 모든 대상 파일 유지 |
| MD/cache/native helper | `md_gbsa_native`, local Prime/Amber/helper 및 reference cache implementation digest의 파일 내용 유지 |
| deleted-MD 원격 probe | outer import와 구분하여 내장 PROBE 바이트·SHA256·자체 Path import 유지 |
| Glide 입력 hash | LigPrep/pose 입력 데이터·저장된 명령/JobId를 변경하지 않음 |

실제 production DB/manifest를 수정해 일치시키거나 live/unknown 작업을 reset하지 않는다. 테스트로 입증한 것은 현 fingerprint/복구 구현의 영향이며, 제3자가 별도로 `_core.py` 전체 checksum을 고정한 미제공 시스템까지 보증하지 않는다.

## 5. Legacy API 조사와 기술 부채

| API | 저장소 실행 경로·테스트 조사 | 동적/외부 가능성 | 처리 |
|---|---|---|---|
| `_core.run_iteration(iteration, args)` | 현재 source·tests·제공된 운영/예제 script에서 직접 호출을 찾지 못했다. 현재 CLI는 driver와 독립 stage를 분기하며 이 runner를 호출하지 않음. 직접 unit test도 찾지 못함 | import 가능한 함수이므로 외부 스크립트나 `getattr` 기반 호출 가능성은 미해결 | **보존**. 이름·시그니처·본문 AST 불변 |
| `_core.select_global_elites(target_iteration, best_count, db_path, diverse_count=0, candidate_pool=0)` | `run_iteration()` 내부에 직접 호출 1곳이 있음. 직접 테스트/다른 실행 caller는 찾지 못함 | legacy runner 또는 외부 importer가 호출할 수 있음 | **보존**. 내부 참조가 있는 함수를 dead code로 삭제하지 않음 |

문서의 옛 prototype/compatibility 설명과 기존 리팩토링 계획에서도 legacy 구현 보존 필요성을 확인했다. 실행 예제, source/tests, 제공된 외부 helper, `tmp/a.py`·`tmp/b.py`에서 추가 caller는 발견하지 못했다. 문서 언급은 실행 caller로 집계하지 않는다.

기술 부채로 다음을 남긴다.

- **TD-L1:** 단일 legacy runner의 지원 범위와 외부 사용 여부가 명시적으로 정리되지 않았고 직접 characterization test가 부족하다.
- **TD-L2:** global elite reader는 과거 모든 generated iteration을 모으는 정책을 포함하여 현재 N−1 기반 stage 정책과 다르다. 사용 확인·호환성 결정을 하지 않고 현재 방식으로 고치거나 삭제하지 않는다.
- 이름이 비슷한 `run_iteration_prime_gbsa()`는 현재 Prime stage와 다수 테스트가 사용한다. 위 legacy runner와 혼동하지 않으며 해당 함수도 변경하지 않는다.

## 6. 테스트와 인터페이스 검증

### 환경과 격리

작업 복사본 `/tmp/graft-t1-8rv5kjro/project`에 별도 `venv --system-site-packages`를 만들고, `--no-deps --no-build-isolation -e`로 **복사본만** 설치했다. PYTHONPATH도 이 복사본으로 고정하고 `graft.__file__` 위치를 확인했다. 기존 의존성을 읽기 전용으로 공유하며 패키지 업그레이드는 하지 않는다.

전체 T0와 같은 Python 3.15.2 / RDKit 2026.09.1 / pytest 9.2.0 환경이다. 완전한 dependency lock 또는 다른 Python/RDKit 버전 검증을 뜻하지 않는다.

### 실행 범위

관련 13개 파일:

```text
test_generation.py, test_generation_sa.py, test_crem_pipeline.py,
test_project_config.py, test_refactoring_contracts.py,
test_glide_pipeline.py, test_glide_recovery.py, test_glide_status_unavailable.py,
test_stage_barriers.py, test_database.py, test_deleted_md_recovery.py,
test_workspace_remote_recovery.py, test_reference_prime_gbsa.py
```

이 기존 테스트들이 생성·설정·CLI·DB·Glide 복구·독립 PROBE 실행을 검사한다. 네 줄 import 삭제에 맞춘 구현 복제 테스트는 추가하지 않는다. 기존 테스트와 기대값은 그대로 사용한다.

추가 확인:

- 여섯 후보 모듈의 module-defined 함수 이름/시그니처 전체 전후 일치. 두 legacy 함수도 포함.
- 네 수정 파일의 AST는 삭제한 top-level import를 제외하면 전체 동일. 문자열 상수/helper 내용도 동일.
- `python -m graft --help`, `generate --help`, `glide --help`, `combichem-3d --help`의 exit/stdout/stderr 전후 동일.
- `python -m graft.stages.generate --help`, `python -m graft.stages.glide --help` 전후 동일.
- Slurm pool의 `main()`은 scheduler 환경과 slot argument가 필요한 실제 calculator entry다. 이를 live 제출로 실행하지 않고 import/compile과 전체 mocked Slurm 회귀로 확인한다.
- package/CLI·pool·deleted-MD module import, `compileall -q src tests` 성공.

| 실행 | 변경 전 | 변경 후 |
|---|---|---|
| 관련 13개 파일 | 361 passed, exit 0 (25.59초) | 361 passed, exit 0 (25.23초) |
| 전체 pytest | 2,646 passed, 3 failed, 7 skipped, exit 1 (85.49초) | 2,646 passed, 3 failed, 7 skipped, exit 1 (83.54초) |
| 전체 warnings | 330 | 330 |
| 전체 case outcome 비교 | T0의 2,656개와 동일 | 변경 전 2,656개와 동일, 신규 실패 0 |
| 알려진 실패 내용 | T0와 같은 assertion/message | 같은 세 node, assertion/message 및 정규화된 traceback 동일 |
| CLI/module help · 시그니처 · helper 문자열 | 기준값 확보 | 동일 |
| CReM/Slurm fingerprint · native PROBE | 기준값 확보 | 동일 |
| compileall · import | 성공 | 복사본 및 적용한 원본 작업 트리에서 성공 |

수정 후 원래 작업 트리의 모든 Python 소스/테스트가 검증한 복사본과 바이트 단위로 동일함을 확인했다. 네 수정 파일은 삭제한 한 줄을 되넣으면 각 작업 시작 SHA256과 일치한다. 나머지 원본 265개 파일은 작업 시작값 그대로다. 시간 차이를 성능 개선으로 해석하지 않는다.

### Known baseline failures 비교

1. `tests/test_combichem_3d.py::test_real_probes_pass_but_actual_fragment_combination_collides_with_itself`: `PASS`와 기대 `CLASH_REJECT` 불일치.
2. `tests/test_md_progress_summary.py::test_driver_pending_line_includes_progress_and_elapsed_even_on_worker_error[0]`: 빈 stdout에 MD pending line을 기대.
3. `tests/test_sequential_driver.py::test_pending_log_uses_persisted_iteration_stage_elapsed[md-gbsa-MD-GBSA still pending for compound 624598]`: 빈 stdout에 MD pending elapsed/current-time을 기대.

전후 및 T0의 case node/status·failure message를 비교한다. pytest fixture repr의 메모리 주소, 잘려 표시된 주소 꼬리, 임시 pytest 디렉터리는 비결정적 진단 정보로 구분하며 과학 값·예상/관찰 verdict·assertion은 정규화하지 않는다. 기존 실패가 다른 원인으로 바뀌거나 새 실패가 생기면 T1을 완료 처리하지 않는다.

## 7. 변경 파일과 사용자 작업 보존

런타임 변경은 다음 네 줄이다.

```diff
--- src/graft/_core.py
-import argparse
--- src/graft/stages/generate.py
-import sqlite3
--- src/graft/stages/glide.py
-import sqlite3
--- src/graft/stages/_deleted_md.py
-from pathlib import Path
```

이번 네 줄만 분리한 [검토·역적용용 patch](t1/imports-only.patch)를 함께 보관했다. 기존 미커밋 변경을 포함한 전체 git diff와 구분한다.

문서 변경은 이 보고서와 연결된 검증 기록뿐이다. 기존 테스트/fixture·TOML·SPEC·REBUILD_PROMPT·설치 metadata 및 나머지 소스는 보존한다. git reset/checkout/전체 파일 되돌리기나 사용자 변경 commit은 하지 않는다. 특히 `_core.py`, `glide.py`의 기존 미커밋 내용은 작업 시작본과 대조하여 위 한 줄 삭제 외에는 동일해야 한다.

운영 DB·manifest·native 결과는 이 작업의 쓰기 대상이 아니다. 외부 계산 옵션·과학 protocol·분자 판정·출력 schema·job identity·worker scheduling·logging은 바꾸지 않는다. Mock/fixture 기반 통과를 production scientific validation으로 표현하지 않는다.

## 8. T2 진행 가능 여부

T1에서 **새로운 T2 차단 사유는 발견하지 못했다.** 기존 실패는 알려진 동일 원인으로 남아 있으며 이 작은 정리와 독립적이다. 다만 T2를 수행할 권한이나 과학적 안전성이 자동으로 확보된 것은 아니다. **별도 승인 후, T2 대상의 보존 테스트와 fingerprint 영향을 다시 확인하는 조건으로 진행 가능**하다.

T2로 넘어갈 때는 FP generator·canonicalization의 기존 invalid/empty·stereo/isotope·반환/예외 계약을 먼저 검증하고, 당시 파일의 fingerprint 포함 여부를 다시 확인해야 한다. T1에서 import 사용 부재를 확인한 것이 T2의 과학적 동등성을 입증하지는 않는다. MD logging과 search/assembly/clash의 구조 변경 보류선은 `T0_KNOWN_FAILURES.md`를 따른다.

**T1만 완료하고 중단한다. T2는 별도 승인 전에는 시작하지 않는다.**
