# T3·T4·T6 실행 결과

> 최신 후속 보완·push 기준은 [PROGRESS](PROGRESS.md)와 [REFINEMENT_REPORT](REFINEMENT_REPORT.md)다. 아래는 당시 결과를 보존한 기록이다.

작성일: 2026-11-15 KST. main 9ec975f 이후의 사용자 미커밋·미추적 변경 및 앞선 T2/T6 변경을 포함한 시작 snapshot을 기준으로 한다. 별도 복사본에서 한 논리 경계씩 테스트한 뒤 반영했다. Git reset/기존 변경 덮어쓰기, production DB·manifest·JobId·결과 수정, native 계산 제출은 하지 않았다.

요청 범위에서 검증 가능한 SQLite·설정·orchestration 경계를 실제 구현에 반영했다. 기존 보류 패치를 그대로 강제 적용하지 않고 최신 코드 및 helper capture/monkeypatch 계약을 재조사했다.

| 항목 | 변경 전 | 변경 후 |
|---|---|---|
| DB 구현 | core에 primitive/schema/state 및 조회 소유권 혼재 | database 소유, core 호환 facade; legacy adapter/역참조는 유지 |
| 설정·초기화 | core loader 및 configure side effects | config loader, project.initialize_project; core configure API/시점 유지 |
| Orchestration | recovery/selection/build/report가 큰 함수에 혼재 | Prime·training·MD·scaffold·Glide·driver의 6개 private 경계 분리 |
| 전체 테스트 | 2,819 passed / 3 failed / 7 skipped | **2,854 passed / 같은 3 failed / 7 skipped** |

현재 기준선은 요청 시 과거 2,701개가 아니라 시작 작업 트리에서 재확인한 2,819 passed다. 이번 추가 35개는 보존 테스트 34개와 config 독립 import 구조 검증 1개다. 기존 테스트 assertion·xfail·skip 변경은 없다. 2,829개 기존 node outcome/정규화한 실패 본문 변화 0. known failures는 self-clash 1개와 MD pending 로그 2개다. 최초 T4 전체 실행의 독립 /proc 테스트 race는 조사·기록했고 runtime/test를 고치지 않은 재실행 및 최종 전체에서 사라졌다.

Runtime 변경 기존 8개: `_core.py`, `database.py`, `cli.py`, `driver.py`, `config.py`, `md_gbsa.py`, `chemistry/scaffold.py`, `stages/glide.py`. 새 runtime `project.py`, 새 test 3개, 단계 보고서/architecture/evidence를 추가·갱신했다. SQL 문자열·transaction 순서와 주요 caller AST 복원, 기존 signature 408개, CLI 5개 help stdout/stderr byte를 검사했다. compileall/import 및 wheel에 새/기존 leaf 배포 확인도 통과했다.

`combichem_slurm.signature()`가 config.py/database.py를 SHA256 입력에 포함한다. 이번 두 파일의 digest는 **변경**되므로 기존 미완료 Slurm 작업이 새 source에서 재개를 거부할 수 있다. 사용자에게 이 영향을 알렸으며 실행 중 프로그램 중단을 허용한 이번 지시에 따라 적용했다. runtime/helper의 fingerprint 검사, allowlist, saved checksum은 변경하지 않았다. 보호 source 40개 중 나머지 38개는 시작 snapshot과 같다.

같은 절대 경로의 synthetic 프로젝트에서 실제 signature()를 호출해 old≠new, old=restored를 확인했다. 실제 live job/입력/native 상태의 재개를 검증한 것은 아니다. old/new 작업을 동일 설치에서 동시에 재개할 수 있다고 주장하지 않는다. 이전 작업 회수가 필요하면 **원래 source 경로에 이전 버전 전체를 복원**하고 다른 입력/상태 guard도 통과해야 한다. 자동 재제출·reset 없이 실패하도록 검사를 유지한다. [배포·복구 절차](t346/RECOVERY.md)를 참조한다.

실제 native 계산/다중 호스트 복구·과학적 수치 동등성은 검증하지 않았다. 기존 결과를 변경하지 않았으며 새로운 과학 정책도 도입하지 않았다. 남은 core SQL/adapter, 깊은 native poll/result loop, global backend install/uninstall(T7), 기존 실패와 /proc 테스트 race는 기술 부채다. T3/T4/T6의 요청한 안전한 범위를 완료했으며 추가 단계 승인 대기는 없다. 다른 계산 기능이나 T7 범위로 확대하지 않는다.

- [T3 상세](T3_COMPLETION_REPORT.md)
- [T4 상세](T4_COMPLETION_REPORT.md)
- [T6 상세](T6_COMPLETION_REPORT.md)
- [Node 비교](t346/comparison.json), [소스/API 감사](t346/source-audit.json), [원본 보존 테스트](t346/original-added-contracts.log)
- [최종 전체 로그](t346/final-full.log), [fingerprint probe](t346/fingerprint-probe.json), [복구](t346/RECOVERY.md)

## 원본 경로 반영 확인

원본 입력 422개를 반영 직전 SHA256으로 확인했다. 의도한 기존 runtime 8개와 문서 5개만 바꾸고 나머지 409개를 보존했다. 추가 파일 충돌은 없었다. 새 project.py→DB/config 구현→core/caller 순서로 파일별 원자적 교체했으며 검증된 source/tests와 bytes가 같다. 독립 runtime 패치 15개와 additive test 패치는 시작 snapshot에 순서대로 replay했을 때 최종 source/tests와 byte가 같았다.

반영 후 원래 설치 경로에서 compileall·package/CLI/config/DB/project/MD/Glide import, diff whitespace 검사를 통과했다. source 복구 스크립트는 archive 및 runtime 8개를 검증한 dry run만 실행했으며 실제 rollback은 하지 않았다. [반영 기록](t346/applied-worktree.json), [원본 경로 검사](t346/root-verification.json)를 보관했다.
