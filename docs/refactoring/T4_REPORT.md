# T4 — TOML loader 소유권 분리, 배포 보류

## 목적·실제 구현·파일

**격리 검증 후보만 구현하고 운영 source에는 적용하지 않았다.** `_core.load_project_toml`의 읽기·해석 구현을 기존 `config.load_project_toml`로 이동했다. 기존 config의 `path` keyword/signature와 core의 `filename` keyword를 각각 유지했다. core는 지연 forwarding wrapper다. config의 top-level core import는 configure_project wrapper 내부로 옮겼다.

후보 파일은 `src/graft/config.py`, `src/graft/_core.py`; 독립 패치는 `held/T4-config-loader.patch`다. 설정 helper의 동적 module lookup은 그대로이며, 중복 self-import만 필요 없게 됐다. config→core는 configure_project 호출 시 남지만 순수 설정 로딩 경로의 core 역참조는 제거된다.

## 보존 범위·검증

관련 설정/경로/reference cache/MD 테스트 308개가 전후 모두 통과했고 개별 node 결과도 동일했다. section/dotted keys, generation method, aliases/defaults, local/remote path, debug scaling, suite/host 우선순위, validation, 초기화와 cache reuse, MD의 기존 설정 경계를 포함한다. 전체 결과는 T9 표에 기록한다.

기존 `configure_project()` 구현·API·migrate/initialize/schema/mkdir 순서는 **전혀 바꾸지 않았다**. loader는 파일 I/O 함수이며 완전한 무부작용 함수라고 부르지 않는다. TOML 읽기만으로 DB/출력 디렉터리를 초기화하지 않는 T0 테스트를 유지했다. 기존 예외·로그·hot-read 구현과 새 설정 schema를 바꾸지 않았다.

## Fingerprint·미검증·보류

config.py는 Slurm source signature 대상이다. 변경 후 digest가 달라지므로 config와 이에 의존하는 core wrapper를 모두 root 적용에서 제외한다. 동등한 테스트 결과로 진행 중 작업의 hash 호환성을 대체하지 않는다. 저장 checksum·manifest·DB·native helper는 변경하지 않았다.

307줄 3D validator의 backend/경로/예외 순서 재분할과 configure_project의 초기화 이동은 보류한다. reference score cache 재시도 marker, migration과 외부 `_core` monkeypatch를 포함한 호출 경계가 넓어 loader보다 위험이 크다. 미제공 외부 module 전체 재바인딩과 native 실제 재개는 미검증이다. T5를 계속 진행한다.
