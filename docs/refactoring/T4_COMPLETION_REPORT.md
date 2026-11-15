# T4 완료 — 설정 해석과 프로젝트 초기화 구분

작성일: 2026-11-15 KST. main 9ec975f 이후의 사용자 미커밋·미추적 변경 및 앞선 T2/T6 변경을 포함한 시작 snapshot을 기준으로 한다. 별도 복사본에서 한 논리 경계씩 테스트한 뒤 반영했다. Git reset/기존 변경 덮어쓰기, production DB·manifest·JobId·결과 수정, native 계산 제출은 하지 않았다.

## 목적·변경 파일

Runtime: `_core.py`, `config.py`, 새 `project.py`.

1. 기존 TOML loader 구현을 config.load_project_toml로 옮겼다. `config(path=...)`와 `_core(filename=...)` keyword/signature를 각각 보존하고 core는 지연 facade를 제공한다.
2. config의 top-level core import를 configure wrapper 안으로 옮겼다. fresh `import graft.config`는 core/DB를 import하거나 DB/native I/O를 시작하지 않는다. loader 자체의 TOML 파일 읽기는 그대로다.
3. `project.initialize_project`가 configure의 migration → reference 초기화 → 조건부 cache restore → schema 확인 → output.mkdir 순서를 소유한다. Namespace 구성/검증/log 설정은 core.configure에 남고 기존 시점에 helper를 호출한다. core의 초기화/open/schema hook은 그때그때 조회한다.
4. 3D 설정의 `_merge_combichem_project_keys`와 `_validate_combichem_values`만 추출했다. alias/default/path/section override 및 numeric/filter 검사 순서, backend 검사 위치는 그대로다. 새 schema/기본값/설정 캐시는 없다. hot-read 코드도 그대로다.

loader의 local self-import를 남겨 두었다. remote_reinvent callback이 remote_aizynth를 재바인딩하는 경우에도 원래 multi-import의 capture 순서를 보존하기 위해서다. 과거 held patch의 단순 self-import 제거 방식은 적용하지 않았다.

## 테스트·동등성

`test_project_boundary_contracts.py` 12개: loader keyword/no-initialization 1개, helper capture 1개, startup 9개 정상·오류·dynamic hook 순서, fresh config import architecture 1개. 앞의 11개는 변경 전후 통과했으며 마지막 1개는 의도한 dependency 경계의 새 검증이다. 원본 source에서 추가 보존 테스트 34개를 재실행할 때는 이 구조 검증 1개만 deselect했다. 전체 suite에서 기존 테스트를 skip/xfail하지 않았다.

loader/bootstrap 관련 전후 319 passed. merge/values 관련 379 passed / 기존 self-clash 1 failed. 경계 추가 검증 185 passed. 전체 재검증 **2,842 passed / 기존 3 failed / 7 skipped**. 원본 2,829 node 결과와 실패 본문 동일. loader·bootstrap 및 두 helper를 원래 자리에 재삽입한 AST의 statement/분기/예외 순서도 동일하다.

첫 전체 실행은 기존 3건 외 Amber timeout 테스트의 /proc TOCTOU가 한 번 발생해 2,841 passed / 4 failed / 7 skipped였다. 즉시 후속 구조 변경을 멈추고 조사했다. 테스트가 `/proc/PID/stat` 존재를 확인한 후 read 사이에 프로세스가 reap되어 ProcessLookupError가 발생했다. 해당 test/amber_runner source는 변경하지 않았다. native 실행을 mock한 진단에서 원본/후보 모두 같은 test-side 예외를 재현했고 해당 파일 11개 재실행, 전체 재실행에서 통과했다. 이 간헐 실패를 기존 baseline 3건에 합치거나 assertion을 완화하지 않았다. [진단](t346/timeout-race-diagnostic.json)과 최초 실패 raw log/XML을 남겼다.

## Fingerprint·미검증·보류

`combichem_slurm.signature()`가 config.py/database.py를 SHA256 입력에 포함한다. 이번 두 파일의 digest는 **변경**되므로 기존 미완료 Slurm 작업이 새 source에서 재개를 거부할 수 있다. 사용자에게 이 영향을 알렸으며 실행 중 프로그램 중단을 허용한 이번 지시에 따라 적용했다. runtime/helper의 fingerprint 검사, allowlist, saved checksum은 변경하지 않았다. 보호 source 40개 중 나머지 38개는 시작 snapshot과 같다.

같은 절대 경로의 synthetic 프로젝트에서 실제 signature()를 호출해 old≠new, old=restored를 확인했다. 실제 live job/입력/native 상태의 재개를 검증한 것은 아니다. old/new 작업을 동일 설치에서 동시에 재개할 수 있다고 주장하지 않는다. 이전 작업 회수가 필요하면 **원래 source 경로에 이전 버전 전체를 복원**하고 다른 입력/상태 guard도 통과해야 한다. 자동 재제출·reset 없이 실패하도록 검사를 유지한다. [배포·복구 절차](t346/RECOVERY.md)를 참조한다.

reference-score marker/restore 정책, initialize_project_database의 과학 입력 처리와 DB migration 구현은 보존했다. private config._core attribute의 임의 외부 의존, traceback line/module 이름 같은 introspection 동등성은 보장하지 않는다. 공개 configure API와 동적 core hooks는 테스트로 유지했다. backend/remote path 해석 전체를 새 객체로 재설계하지 않았다. T6까지 진행 후 전체 검증을 완료했다.

[검증 비교](t346/comparison.json), [구현/의존성 감사](t346/source-audit.json).
