# T9 — 자율 리팩토링 최종 검증 및 적용 결과

작성일: 2026-11-14 (KST). 기준: 사용자 승인 범위 T2-B–T9 및 시작 시점의 미커밋/미추적 작업 트리. 기존 사용자 변경은 git HEAD로 되돌리지 않았다.

## 1. 결과와 단계별 판단

**안전하게 적용 가능한 변경을 완료했고, 신규 회귀는 0건이다.** 최종 전체 결과는 **2,718 passed / 기존 3 failed / 7 skipped**다. 기존 2,711개 case의 outcome, 실패 message 및 assertion 본문이 같고 신규 17개가 모두 통과했다. 기존 실패를 수정하거나 숨기지 않았다. 전체 suite가 green이라는 뜻은 아니다.

| 단계 | 실제 결과 | 배포/적용 상태 |
|---|---|---|
| T2-B | canonicalizer 구현을 chemistry leaf로 이동, core wrapper 보존 | 적용 |
| T2 나머지 | 공유 FP generator·similarity lookup·DB 호출 유지 | 변경 보류; 공유 객체/pose 선택/DB fingerprint 경계 |
| T3-A | status의 두 SQL을 DB helper로 이동, 기존 connection/context 사용 | 격리 검증 완료, 패치만 보관; runtime 미적용 |
| T3 나머지 | 연결/schema/state factory·driver raw query 검토 | transaction/adapter와 fingerprint 위험으로 유지 |
| T4-A | config가 loader 소유, core facade 유지, config의 core import 지연 | 격리 검증 완료, 패치만 보관; runtime 미적용 |
| T4 나머지 | configure 초기화와 3D validator 분할 검토 | migration/cache marker/예외 순서 보존 우선, 유지 |
| T5 | 계약이 동일한 Prime/LigPrep/Glide JSON 저장 3개 통합 | 적용; Amber·fsync/native 정책은 유지 |
| T6-A/B | Glide 그룹 claim loop, driver pending 보고를 각각 추출 | 각 변경 독립 검증 후 적용 |
| T6 나머지 | Prime, MD, LibInvent training, scaffold 함수 개별 검토 | 네이티브 회수/선택/과학 입력 계약 위험이 더 커 유지 |
| T7 | 전역 worker 선택·fork/remote/Slurm 경로 검토 | fingerprint 및 child callable/recovery 결합으로 유지 |
| T8 | 새 경계의 국소 타입/docstring, leaf import 2개 테스트 | 적용; 새 framework/전역 타입 규칙 없음 |
| T9 | 아키텍처·소유권·배포 구분, 전체 회귀·API·CLI·패키지 확인 | 완료 |

단계별 목적/범위/검증/위험은 [T2](T2_REPORT.md), [T3](T3_REPORT.md), [T4](T4_REPORT.md), [T5](T5_REPORT.md), [T6](T6_REPORT.md), [T7](T7_REPORT.md), [T8](T8_REPORT.md)에 있다. 중간 승인 요청 없이 독립 작업으로 이어갔다.

## 2. 아키텍처 전후

| 경계 | 변경 전 | 실제 적용 후 |
|---|---|---|
| Canonical SMILES | core에 직접 구현, DB/생성 caller가 core 호출 | chemistry.smiles가 구현 소유; 기존 core 경로는 지연 wrapper |
| Chemistry 초기화 | core를 통해 접근해야 canonicalizer 사용 가능 | 새 leaf는 core/config/DB 로딩 없이 독립 import 가능 |
| JobId JSON | 세 module에 같은 3줄 저장 절차 중복 | 기존 경로/API wrapper + 작은 _job_record helper 하나 |
| Glide | _run_stage 안에 그룹별 claim/filter와 계산 흐름 혼재 | claim/filter 정책을 별도 helper로 분리; 제출/회수/commit 순서 유지 |
| Driver | supervisor 안에 pending 보고와 제어 흐름 혼재 | 보고 helper가 기존 key를 반환; worker/완료/대기는 supervisor 소유 |
| DB/config 소유권 | core 역참조와 초기화 facade | 적용 tree에서는 유지. 검증 후보와 실제 적용을 명확히 분리 |

[architecture.md](../architecture.md)를 현재 단일 iteration 순차 감독, worker 내부 병렬 계산, 실제 state vocabulary, durable recovery, DB/화학/저장 소유권과 보류 후보에 맞췄다. SPEC/REBUILD_PROMPT의 요구사항과 불확실성 label은 바꾸지 않았다.

AST import 재분석: source module 112개, top-level 다중 module SCC 0개. 함수 내부 import까지 포함하면 최대 47개 module의 논리 SCC가 여전히 남는다. `_core↔database` 순환이 제거됐다고 주장하지 않는다. 동적 import·내장 문자열/외부 script까지 이 AST 분석이 완전히 포괄하지는 않는다.

## 3. 변경 파일과 리뷰 단위

적용한 runtime 파일:

- src/graft/_core.py — canonicalize_smiles wrapper만 변경
- src/graft/chemistry/smiles.py — 새 canonicalizer 구현
- src/graft/_job_record.py — 새 공통 로컬 JSON publication
- src/graft/prime_gbsa_async.py, ligprep_recovery.py, glide_recovery.py — 기존 저장 wrapper 유지
- src/graft/stages/glide.py — 그룹 claim helper 추출·새 helper 타입
- src/graft/driver.py — pending 보고 helper 추출·새 helper 타입

추가 테스트는 tests/test_job_record_contracts.py(15 case), tests/test_refactoring_leaf_imports.py(2 case)다. 기존 test 파일/기대값은 수정하지 않았다. docs/architecture.md, T2–T9 보고서와 검증 증거·패치를 추가했다. 이번 작업에 egfr.toml, 운영 DB/manifest/output/native helper 변경은 없다.

[patches/](patches/)에는 **시작 시점의 사용자 작업 트리**를 기준으로 T2 → T5 → T6-A → T6-B → T8의 작은 논리적 패치가 있다. git HEAD 기준의 무관한 사용자 변경은 포함하지 않는다. 임시 디렉터리에서 순서대로 replay하여 최종 source/tests와 byte 일치를 확인했다. 되돌릴 때 후속 의존 패치를 역순으로 고려해야 한다. git commit/push는 수행하지 않았다.

[held/](held/)의 T3-status-sql.patch와 T4-config-loader.patch는 **미적용 후보**다. 최종 적용 tree에서 git apply --check가 통과한다. 적용해도 된다는 배포 승인은 아니며, 아래 fingerprint 제약이 먼저 해결돼야 한다.

## 4. 검증 결과

| 검증 | 결과 |
|---|---|
| 시작 시 전체 baseline | 2,701 passed / 기존 3 failed / 7 skipped |
| 안전한 테스트 환경에서 원본 control 재검증 | 동일; 87.08초 |
| T2 관련 / 기존 T2-A chemistry 포함 | 149 passed |
| T3 전후 | 50 passed / 기존 MD 로그 1 failed, 51 node 동일 |
| T4 전후 | 308 passed, 308 node 동일 |
| T3+T4 격리 후보 전체 | 2,701 passed / 동일 3 failed / 7 skipped; 84.95초 |
| T5 전후 / 추가 characterization 15 포함 | 253 passed, 253 node 동일 |
| T6 Glide 전후 | 142 passed, 142 node 동일 |
| T6 driver 전후 | 165 passed / 기존 MD 로그 2 failed, 167 node 동일 |
| T7 관련 backend/recovery | 85 passed |
| T8 전후 / leaf import 2 포함 | 237 passed / 기존 MD 로그 2 failed, 239 node 동일 |
| 최종 적용 후보 전체 | **2,718 passed / 동일 3 failed / 7 skipped; 83.36초** |
| 기존 2,711 node 전후 비교 | outcome/message/assertion 변화 0개; 추가 17개 모두 passed |
| compileall -q src tests / package·CLI·새 leaf import | 성공 |
| 기존 함수·메서드 선언 AST 948개 | 삭제·인자·반환 annotation 변경 0개 |
| CLI/module help 7개 진입 경로 | control과 stdout/stderr byte 동일, exit 0 |
| core의 기존 다중 행 문자열 99개 | byte 동일; 내장 helper payload 보존 |
| 조사한 source fingerprint/native 보호 파일 40개 | 시작 시점과 SHA256 동일 |
| wheel build / 새 두 module 포함 확인 | 성공; 의존성·운영 설치 변경 없음 |
| 리뷰 패치 replay / 보류 패치 apply --check | 성공 |

시간은 테스트 실행 경과이며 성능 개선 주장이 아니다. Python 3.15.2 / RDKit 2026.09.1 / NumPy 2.5.4 / pytest 9.2.0 및 기존 의존성을 공유한 별도 venv를 사용했다. 330 warnings는 기존 multi-threaded fork DeprecationWarning이며 start method를 바꾸지 않았다. 7개 skip은 기존 Torch 미설치 때문이다.

Known failure node ID:

```text
tests/test_combichem_3d.py::test_real_probes_pass_but_actual_fragment_combination_collides_with_itself
tests/test_md_progress_summary.py::test_driver_pending_line_includes_progress_and_elapsed_even_on_worker_error[0]
tests/test_sequential_driver.py::test_pending_log_uses_persisted_iteration_stage_elapsed[md-gbsa-MD-GBSA still pending for compound 624598]
```

첫 실패의 기대 CLASH_REJECT/관찰 PASS와 두 MD 기본 verbosity 출력 기대 충돌은 [T0 분석](T0_KNOWN_FAILURES.md) 그대로다. 신규 회귀로 계산하지도, xfail/skip으로 숨기지도 않았다.

비교 시 memory address, 격리 checkout/temp root를 정규화했다. pytest가 경로 길이에 따라 잘라 표시하는 **fixture argument header**는 비교에서 분리하고 원본 로그에 남겼다. failure message와 `def test_`부터의 assertion/source/expected-vs-actual 본문을 비교했다. assertion·분자 결과·진단 값은 완화하지 않았다. [comparison JSON](t2-t9/final-full-comparison.json)과 원본 XML/log를 함께 보관한다.

## 5. 검증 환경에서 발견한 대기와 격리

초기 T3/T4 전체 실행 하나는 테스트 프로세스가 `/tmp/graft-schrodinger-submit-1023.lock`에서 대기했다. read-only /proc FD/wait 상태로 공용 lock 대기를 확인했으며, lock을 삭제/해제하거나 운영 프로세스에 신호를 보내지 않았다. 해당 **테스트 프로세스만** 중단했고 중단 로그/exit -15를 남겼다. 완료된 회귀 검증으로 세지 않는다. 정확히 어떤 fixture lifetime/호출이 공용 tempfile binding을 조회했는지까지 확정하지 않았다.

재검증은 프로세스 시작부터 독립 TMPDIR를 지정하고, 테스트 fixture 밖에서도 session-local MD registry를 사용하도록 외부 pytest harness를 적용했다. 테스트 원본/기대값과 production runtime 파일에는 이 harness를 넣지 않았다. 같은 harness에서 변경 전 원본 control과 후보 전체를 각각 다시 실행해 기준선이 그대로임을 확인했다. 단순 재시도 통과로 대기를 코드 수정 효과라고 주장하지 않는다.

격리 작업 디렉터리: `/tmp/graft-t2-t9-60av2y50`; editable install은 이 복사본의 venv에만 했다. 원본 운영 설치를 재설치하지 않았다. [isolation.py](t2-t9/isolation.py), 실행 command JSON, [control XML](t2-t9/control-baseline.xml), [최종 XML](t2-t9/final-full.xml), [최종 로그](t2-t9/final-full.log)를 보관한다.

## 6. Fingerprint와 배포 판단

- **실제 적용분:** 조사한 source guard 입력 40개와 native helper/payload를 변경하지 않았다. 기존 DB·manifest·저장 checksum/JobId를 수정하거나 native helper를 덮어쓰지 않았다. source validation과 allowlist는 그대로다. 새 chemistry/smiles.py 및 _job_record.py가 포함된 package 단위 배포가 필요하며 wheel 포함을 검증했다.
- **T3/T4:** database.py/config.py는 combichem_slurm.signature의 직접 SHA256 입력이다. 격리 후보는 테스트가 동등해도 이 digest가 달라진다. 진행 중 작업을 새 digest로 재개할 수 있다는 증거가 없어 root runtime에 적용하지 않았다. native/remote 배포 전환은 별도 결정 사항이다.
- **T7:** combichem*.py 및 remote implementation hash가 바뀌므로 callable 선택 개편은 보류했다.
- 현재 hash 정책이 모든 전이 dependency를 포괄한다는 보증은 아니다. fingerprint에 없는 파일이라는 이유로 과학 동작 변경을 허용하지 않았다. live job restart나 cluster 전체 rolling deployment를 검증한 것은 아니다.

## 7. 남은 기술 부채·별도 후속 작업

1. 기존 실패 3건의 테스트 기대 계약 정리는 별도 수정으로 분리한다. self-clash 과학 검토와 MD verbosity 기대 충돌을 이번에 수정하지 않았다.
2. T3/T4 소유권 개선은 source digest 변경이 허용되는 배포 전환 정책이 마련된 뒤 작은 후보부터 재검증한다. 기존 작업을 reset하거나 manifest checksum을 보정하는 방식은 제안하지 않는다.
3. FP_GENERATOR의 mutable binding과 DB→core 호출은 유지했다. 객체 동일성/외부 patch/references·pose 선택을 먼저 보존해야 한다.
4. Prime/MD 회수, LibInvent/scaffold 선택 함수는 현재 분기/side-effect 순서가 큰 계약이다. 추가 event/byte/native 증거 없는 분할은 보류했다. Torch 설치와 실제 과학 계산 검증은 별도 환경 작업이다.
5. Distributed 전역 mutation의 반복 backend 선택·child callable/pickling·fork recovery 검증을 강화한 뒤 명시적 선택 전환을 검토한다.
6. 기존 Legacy API run_iteration/select_global_elites, SQLite adapter/context 의미, status 초기화 부작용은 보존했다. 미제공 외부 스크립트·module 전체 rebinding은 완전 조사할 수 없다.
7. T2-A에서 관찰한 invalid/None canonical identity와 import_crem_results의 dedup/NOT NULL 경계는 그대로다. validation bug/hardening 여부는 별도 조사 대상이다.
8. native scientific accuracy, 실제 SSH/Slurm/JobServer 재개, 새 OS/start method, 라이브 작업의 hot deployment는 검증하지 않았다. Mock/event/임시 DB 테스트를 그 증거로 대신하지 않는다.

추가 기능·성능 최적화·새 설정 schema·전역 logging/formatter 변경은 포함하지 않았다. 안전한 적용, 위험한 후보의 분리, 전체 회귀와 문서화를 완료하여 **T9에서 작업을 마친다**.

## 8. 최종 작업 트리 확인

검증된 source/tests와 byte가 같은 변경만 원래 작업 트리에 반영했다. 새 구현 module을 wrapper보다 먼저 배치했고 기존 파일은 SHA256 확인 후 원자적으로 교체했다. 시작 시 수집한 기존 입력 279개 중 의도한 7개(소스 6개와 architecture.md)만 변경했고 나머지 272개는 그대로다. 기존 테스트·config.py/database.py·SPEC/HANDOFF/REBUILD 및 수집한 example 설정은 보존했다. 초기 source fingerprint에 걸리는 후보는 적용하지 않았다.

반영 후 원본 경로에서 compileall, package/CLI/두 새 module import와 canonicalizer smoke check를 다시 확인했다. diff whitespace 검사도 통과했다. source/tests는 전체 suite를 통과한 격리 후보와 동일하므로 같은 전체 검사를 불필요하게 반복하지 않았다. [반영 기록](t2-t9/applied-worktree.json)에 파일별 digest와 사용자 변경 보존 검증을 남겼다. 운영 DB/manifest/result 파일은 반영 대상에 포함하지 않았으며, 외부에서 동시에 진행되는 작업의 상태가 멈춰 있었다고 주장하지 않는다.
