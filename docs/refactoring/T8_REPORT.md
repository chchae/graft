# T8 — 변경한 경계의 타입·오류 계약 명시

## 목적·수정 범위

새 `_report_pending_stage`의 iteration/stage/rc/reason/previous_pending 및 반환 tuple에 타입을 명시하고, 새 Glide claim helper의 db_path에 str|Path를 명시했다. 새 chemistry leaf의 None 반환·RDKit 예외 전파를 docstring으로 설명했다. 새 JSON helper의 path/record/None 반환 타입은 T5에서 이미 명시했다.

runtime 파일: `driver.py`, `stages/glide.py`, `chemistry/smiles.py`의 새 경계에 한정한다. 기존 공개 함수 signature·예외 타입·message·timing·log/marker를 바꾸지 않았다. dict schema를 새로 만들지 않고 기존 row/Namespace 형태를 유지한다. TypedDict, formatter, 프로젝트 전체 타입 검사 규칙을 도입하지 않았다. str annotation 아래 non-string을 None으로 처리하는 기존 canonicalizer runtime도 바꾸지 않았다.

## 검증

새 `tests/test_refactoring_leaf_imports.py`의 2개 case는 fresh Python에서 새 chemistry/JSON leaf를 import할 때 core/config/database를 로드하거나 SQLite 연결·subprocess를 시작하지 않는 경계를 확인한다. 반환값을 새로 정하는 테스트가 아니다. T5의 15개와 합쳐 이번 작업의 신규 case는 17개다.

관련 239개는 전후 237 passed / 기존 MD 로그 2 failed로 동일하고 node·정규화 assertion 내용 변화는 0개다. 기존 예외 catch나 print→logging 전환은 하지 않았다. compile/import/CLI/public signature 검사는 T9에 기록한다.

## Fingerprint·미검증·다음 단계

이 단계 파일은 조사한 source fingerprint 목록 밖이다. native helper/저장 checksum/manifest·DB 변경은 없다. strict type checker 전체 통과나 모든 dict payload의 정적 검증은 주장하지 않는다. native scientific equivalence는 미검증이다. T9 최종 검증·문서화를 진행한다.
