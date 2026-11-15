# T6 재개 — 입력 준비·선행 확인·파일 쓰기·검증 경계 분리

> 2026-11-15 후속 적용: [T6_COMPLETION_REPORT.md](T6_COMPLETION_REPORT.md)가 현재 상태다. 아래 본문은 당시 실행·보류 상태의 기록으로 보존한다.

작성일: 2026-11-15 (KST). 기준은 main 9ec975f 위의 기존 미커밋·미추적 변경을 포함한 현재 작업 트리다. 사용자 지시로 T6을 재개했다. 한 번에 한 경계만 수정하고 관련 테스트를 전후 실행했다. T3/T4/T7의 보류 패치는 적용하지 않았다.

## 1. 실제 적용 범위와 목적

| 작업 | 변경한 경계 | 보존한 핵심 동작 |
|---|---|---|
| T6-C | _core.run_iteration_prime_gbsa → _prepare_prime_gbsa_input_files | 새 계산의 TSV/IDs·수용체·PV 준비를 분리. 저장 JobId 회수·ranking/기존 score 제외·claim·guard·batch 생성/폴링 순서 유지 |
| T6-D | md_gbsa.run → _md_upstream_ready | 새 selection 전에 기존 Prime 기록→native 단계→실행 중 worker를 확인하는 절차 분리. 이후 DB readiness·freeze·remote check·제출/폴링/commit은 유지 |
| T6-E | write_elite_libinvent_training_files의 내부 writer → _write_libinvent_training_pairs | 과학적 decomposition·vocabulary 필터·seed 12345 split·metadata 순서 유지. pair token 검증과 파일 쓰기 경계만 분리 |
| T6-F | prepare_iteration_scaffold → _validated_docking_smarts | snapshot/MCS 선택·fallback은 그대로. attachment 및 docking core 검증을 DB 저장/파일 게시와 분리 |

변경한 runtime 파일은 `src/graft/_core.py`, `src/graft/md_gbsa.py`, `src/graft/chemistry/scaffold.py` 세 개다. 기존 module 내 private helper로 추출했고 새 framework나 클래스는 없다. 공개/Legacy 함수 인자·반환 annotation을 바꾸지 않았다.

원래 함수의 statement를 새 helper 호출 위치에 다시 넣어 전체 AST를 비교했다. core는 pair writer의 원래 내부 함수도 복원해 비교했고, MD는 helper의 False 반환을 원래 early return으로 복원했다. 세 module 모두 원래 AST와 같았다. 대상 함수 외 기존 top-level 함수 97개의 AST도 그대로다. 이는 statement/분기/명령 literal 보존 증거이며 모든 Python scope/introspection·외부 실행의 동등성을 완전히 보장한다는 뜻은 아니다.

## 2. 먼저 추가한 characterization test

| 파일 | case | 실제 고정한 계약 |
|---|---:|---|
| test_prime_orchestration_contracts.py | 23 | reference/generated × 수용체 복사/동일 경로/grid × native/local; TSV CRLF/UTF-8/quoting·ID 순서·reference map; 준비 실패·missing pose/receptor; 3개 저장 mode 및 legacy 결과 게시→JobId 삭제 순서 |
| test_md_upstream_contracts.py | 18 | 기존 Prime/4개 native 단계/8개 worker blocker의 순서와 로그·early return; query 예외 instance 전파; disabled 무작업; readiness 이전 native/claim 금지 |
| test_libinvent_pair_file_contracts.py | 10 | 5/6-column elites, score_source header, 결정적 train/valid bytes·metadata·stdout, token 오류 및 파일 open 오류 시 기존 partial-write·예외 순서, usable<2의 기존 파일 보존 |
| test_scaffold_publication_contracts.py | 8 | 저장 snapshot 재사용, legacy/explicit docking 문자열·JSON byte/order, DB 저장→4개 파일 게시→Namespace 변경 순서, invalid MCS/containment/reference/attachment 오류와 publication 실패 |

각 새 테스트를 해당 runtime 변경 **전에** 통과시킨 뒤 같은 테스트 묶음을 변경 후 다시 실행했다. 기존 test 파일·기대값·xfail/skip은 바꾸지 않았다. Mock native 함수는 임시 파일만 만들며 실제 계산을 제출하지 않는다. Scaffold 검증에는 실제 synthetic RDKit molecule과 임시 SQLite를 사용한다.

새 scaffold 테스트 초안 두 case는 JSON key 순서를 입력 dict 순서로 잘못 예상했다. runtime을 수정하기 전 실행에서 DB가 이미 sort_keys로 저장한 snapshot을 읽어 출력함을 확인했고, 새 테스트 oracle을 그 기존 byte 순서로 고정했다. 해당 초안 실패 로그를 별도 보관했다. 기존 assertion이나 프로그램 JSON 형식은 바꾸지 않았다.

## 3. 검증 결과

| 실행 | 변경 전 | 변경 후 |
|---|---|---|
| Prime 관련 | 267 passed | 267 passed |
| MD 관련 | 258 passed | 258 passed |
| Training 관련 | 161 passed / 기존 7 skipped | 동일 |
| Scaffold 관련 | 216 passed | 216 passed |
| 전체 suite | 2,718 passed / 기존 3 failed / 7 skipped | **2,777 passed / 동일 3 failed / 7 skipped** |

기존 2,728개 node의 outcome·실패 message·assertion 본문 변화는 **0개**다. 추가 59개 모두 통과했다. Pytest fixture header와 memory address·격리 temp 경로만 정규화했고 raw XML/log를 함께 남겼다. 테스트 시간은 baseline 86.37초, 최종 83.50초이며 성능 최적화 주장이 아니다.

기존 세 실패는 그대로다:

```text
tests/test_combichem_3d.py::test_real_probes_pass_but_actual_fragment_combination_collides_with_itself
tests/test_md_progress_summary.py::test_driver_pending_line_includes_progress_and_elapsed_even_on_worker_error[0]
tests/test_sequential_driver.py::test_pending_log_uses_persisted_iteration_stage_elapsed[md-gbsa-MD-GBSA still pending for compound 624598]
```

Compileall, graft/CLI/MD/scaffold import 성공. graft/driver/Prime-stage/MD-stage help stdout·stderr byte도 전후 동일하다. 기존 Torch 부재에 따른 7개 skip과 multi-threaded fork warning은 유지한다.

## 4. Fingerprint와 운영 보존

수정한 세 파일은 조사한 CReM/standalone/Slurm/remote/native implementation hash 목록 밖이다. 이전 audit의 보호 source/native 파일 40개가 이번 시작 snapshot과 SHA256 동일하다. config.py/database.py, combichem*.py/combichem3d, native MD/Prime/dispatch helper, guard/allowlist를 수정하지 않았다. 계산 helper를 생성하는 기존 함수들의 AST도 유지됐다.

source fingerprint 우회, 저장 checksum·DB·manifest 수정, native helper 업로드/덮어쓰기, 기존 작업 reset/relaunch를 하지 않았다. 테스트는 별도 복사본 `/tmp/graft-t6-restart-9y41oc2e/project`와 venv/TMPDIR/MD registry에서 실행했다. 운영 설치를 재설치하지 않았다. 단순 import 이후 실행되는 과학 계산·native 상태 조회도 수행하지 않았다.

현재 fingerprint 정책이 모든 전이 dependency를 포괄한다는 주장은 하지 않는다. 실제 live job 재개와 native scientific equivalence는 미검증이다. 이번에 MD 슬롯 수·admission/poll 빈도·retry·진행 수 표시를 고치지 않았다.

## 5. 리뷰·복구와 남은 범위

[패치](t6-restart/patches/)는 T6-C→D→E→F의 작은 단위이며 현재 작업 시작 snapshot을 기준으로 한다. 별도 임시 디렉터리에서 순서대로 replay하여 최종 source/tests byte 일치를 확인했다. C/E는 같은 core를 순차 변경하므로 되돌릴 때 후속 의존 변경을 고려한다. 기존 사용자 변경을 git HEAD로 reset하지 않는다.

Prime saved-mode 회수와 native 제출, MD 폴링/후처리/commit 루프, LibInvent decomposition/vocabulary와 MCS selection/fallback 알고리즘은 유지했다. 이번 T6 재개는 이 함수들의 안전하게 검증한 책임 경계를 분리한 것이다. 모든 scientific/orchestration 코드를 재작성하거나 향후 구조 개선 여지가 없어졌다는 뜻은 아니다.

추가 native event transcript/입출력 증거와 실제 실행 환경이 필요한 깊은 복구·과학 선택 변경, T3/T4/T7 및 기존 실패 수정은 별도 후속 작업으로 남긴다. 임의의 다음 단계 확대나 native 계산 제출은 하지 않았다.

[전후 node 비교](t6-restart/comparison.json), [소스/API 검사](t6-restart/source-audit.json), [전체 로그](t6-restart/final-full.log)를 보관한다.

원래 orchestration 함수 길이(변경 전→후, 줄 수)는 다음과 같다. 이는 목표 자체가 아니라 책임 이동 범위를 보여주는 보조 정보다.

- `run_iteration_prime_gbsa`: 221 → 121
- `run`: 156 → 146
- `write_elite_libinvent_training_files`: 236 → 215
- `prepare_iteration_scaffold`: 171 → 151

## 6. 최종 반영 확인

검증된 source/tests와 byte가 같은 내용만 기존 파일 SHA256 확인 후 원자적으로 반영했다. 기존 입력 353개 중 의도한 runtime 3개와 문서 2개만 변경했고 나머지 348개를 보존했다. 반영 후 원본 경로에서 compileall·package/CLI/MD/scaffold import 및 diff whitespace 검사도 통과했다. 원본 DB·manifest·outputs/native helper와 다른 사용자 source/test 변경은 덮어쓰지 않았다. [반영 기록](t6-restart/applied-worktree.json)을 보관한다.
