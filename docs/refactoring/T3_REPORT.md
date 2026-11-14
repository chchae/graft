# T3 — 작은 DB 조회의 소유권 분리, 배포 보류

## 목적·변경 파일·실제 구현

**격리 검증 후보만 구현했고 운영 source에는 적용하지 않았다.** `database.status_rows(conn)`가 CLI status의 기존 두 SELECT를 그대로 소유하도록 옮겼다. `cli._status`는 기존 `_core.configure_project`와 `_core.open_sqlite` context 안에서 helper를 호출하고 기존 출력 코드를 그대로 실행한다.

후보 파일: `src/graft/database.py`, `src/graft/cli.py`. `held/T3-status-sql.patch`로 독립 검토할 수 있다. 새로운 connection factory/context manager를 만들지 않았다. 같은 connection에서 같은 순서로 execute/fetchall하고, context 진입/종료·commit/rollback·connection 반환형 및 close 의미를 바꾸지 않았다. status의 legacy reference metadata backfill 시점도 그대로다.

## 테스트·보존 API

관련 51개 case 전후 결과는 50 passed / 기존 MD 로그 1 failed로 동일하다. node ID, 정규화한 실패 본문이 동일하며 신규 실패는 없다. T0 stdout fixture 및 실제 임시 SQLite 과학 row/descriptor 보존 검증을 포함한다. 격리 후보 전체 결과는 T9 검증 표를 참조한다.

기존 `_core` 연결/schema/state API, database.connect, CLI 명령·인자·표/정렬·출력·초기화를 보존한다. migration/WAL/FK/claim/selection 구현은 변경하지 않았다.

## Fingerprint·미검증·보류

`combichem_slurm.signature()`는 database.py를 SHA256 입력으로 포함한다. SQL 이동만으로도 파일 digest가 달라져 기존 미완료 작업에서 source mismatch가 발생할 수 있다. 테스트 통과는 저장된 작업 재개 호환성의 증거가 아니다. 따라서 db와 이를 호출하는 cli 변경 **둘 다** root 적용에서 제외하고 패치로 남겼다. allowlist/검사 우회/manifest 수정/기존 작업 재시작은 없다.

`open_sqlite/create_sqlite_schema/ensure_schema_columns/set_compound_state`의 소유권 이동과 driver의 남은 raw query는 보류한다. 같은 fingerprint 제약 외에도 SQLiteCursor adapter·context와 migration의 역참조가 얽혀 있다. 실제 native/다중 호스트 재개는 미검증이다. T4를 독립적으로 진행했다.
