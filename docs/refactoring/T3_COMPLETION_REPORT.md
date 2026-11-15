# T3 완료 — SQLite 구현 소유권 정리

작성일: 2026-11-15 KST. main 9ec975f 이후의 사용자 미커밋·미추적 변경 및 앞선 T2/T6 변경을 포함한 시작 snapshot을 기준으로 한다. 별도 복사본에서 한 논리 경계씩 테스트한 뒤 반영했다. Git reset/기존 변경 덮어쓰기, production DB·manifest·JobId·결과 수정, native 계산 제출은 하지 않았다.

## 목적과 변경

| 작업 | 구현 위치 | 보존한 계약 |
|---|---|---|
| T3-01 | database.status_rows / cli._status | caller-owned connection에서 기존 2 SELECT/순서/formatting |
| T3-02 | database.open_sqlite / _core facade | native sqlite3.Connection, WAL·busy_timeout=30000·FK, 실패 시 close |
| T3-03 | database.create_sqlite_schema / ensure_schema_columns | 비파괴 schema, legacy state 1회 보정, ensure의 기존 명시적 commit |
| T3-04 | database.set_compound_state | empty 입력 무작업, path 오류, dynamic _core.open_sqlite, 명시적 commit |
| T3-05 | database의 7개 legacy 조회 + driver 조회 2개 | row/정렬/None·0 구분, adapter/context, canonicalizer patch 지점 |

Runtime 변경 파일: `_core.py`, `database.py`, `cli.py`, `driver.py`. 기존 `_core` 함수 11개의 signature/docstring/호출 경로를 forwarding facade로 보존했다. 조회는 `find_iteration_for_state`, `get_max_iteration`, `get_iteration_counts`, `get_existing_canonical_smiles`, `get_reference_name`, `state_counts`, `list_iteration_compounds`다. `database.connect()`는 여전히 현재 `_core.open_sqlite(Path(path))`를 호출한다.

SQL literal/분기/commit 순서는 그대로 이동했다. driver.choose_iteration의 서로 다른 두 connection context도 합치지 않았다. MAX의 기존 0 반환과 retry 조회의 None 반환은 별개다. schema helper들의 local self-import는 불필요해 보여도 multi-import capture 및 뒤늦은 MD helper 조회 순서를 위해 보존했다. SQLiteConnection/Cursor adapter는 core에 남는다. 상태 갱신/조회가 legacy monkeypatch를 지키기 위해 core를 참조하므로 **core↔database 논리 순환이 완전히 사라진 것은 아니다.**

## 선행 테스트 및 결과

`tests/test_database_boundary_contracts.py` 11개로 context commit/rollback·미자동close, adapter SQL 변환·close, 기존 pending transaction commit, helper capture 순서, 상태 갱신 예외 instance/commit/중복 ID, 작은 조회와 dynamic canonicalizer를 고정했다. 초기 9개는 연결 이동 전, 나머지 2개는 조회 이동 전에 통과했다. 최종 추가 보존 테스트를 원본 source에 다시 실행한 결과도 통과했다.

초기 관련 146 passed / 기존 MD 로그 1 failed, status/open/schema/state 단계별 59 passed / 같은 1 failed, 조회 단계 전후 61 passed / 같은 1 failed. T3 전체는 **2,830 passed / 기존 3 failed / 7 skipped**. 기존 node outcome 및 실패 본문 변화 0, 추가 11개 통과. 최종 T3+T4+T6 전체는 2,854 passed / 동일 3 failed / 7 skipped다.

## Fingerprint·제한·남은 범위

`combichem_slurm.signature()`가 config.py/database.py를 SHA256 입력에 포함한다. 이번 두 파일의 digest는 **변경**되므로 기존 미완료 Slurm 작업이 새 source에서 재개를 거부할 수 있다. 사용자에게 이 영향을 알렸으며 실행 중 프로그램 중단을 허용한 이번 지시에 따라 적용했다. runtime/helper의 fingerprint 검사, allowlist, saved checksum은 변경하지 않았다. 보호 source 40개 중 나머지 38개는 시작 snapshot과 같다.

같은 절대 경로의 synthetic 프로젝트에서 실제 signature()를 호출해 old≠new, old=restored를 확인했다. 실제 live job/입력/native 상태의 재개를 검증한 것은 아니다. old/new 작업을 동일 설치에서 동시에 재개할 수 있다고 주장하지 않는다. 이전 작업 회수가 필요하면 **원래 source 경로에 이전 버전 전체를 복원**하고 다른 입력/상태 guard도 통과해야 한다. 자동 재제출·reset 없이 실패하도록 검사를 유지한다. [배포·복구 절차](t346/RECOVERY.md)를 참조한다.

11개 구현의 원래 AST를 core attribute qualifier만 복원해 비교했고 동일했다. migration/atomic claim/frozen selection SQL 본문, commit 경계, molecule identity는 변경하지 않았다. native 다중 호스트 실행 및 임의 외부 module 전체 재바인딩은 미검증이다. core에 남은 business SQL/adapter까지 일괄 이동하지 않았다. T4/T6으로 계속 진행했고 최종 검증을 마쳤다.

[전체 비교](t346/comparison.json), [감사](t346/source-audit.json), [논리별 패치](t346/patches/).
