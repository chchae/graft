# T2 — Chemistry 구현 소유권의 첫 분리

## 목적과 적용 범위

`canonicalize_smiles` 하나의 구현을 `src/graft/chemistry/smiles.py`로 그대로 옮겼다. `_core.canonicalize_smiles(smiles: str) -> str | None`는 함수 내부 import와 forwarding으로 유지한다. 새 chemistry leaf는 RDKit만 직접 참조한다. 공개 함수의 module/name/keyword `smiles`/signature와 기존 caller를 유지했다.

변경 파일: `src/graft/_core.py`, `src/graft/chemistry/smiles.py`. 기존 구현 body는 그대로다. FP_GENERATOR의 객체·생성 시점·radius 2/2048 bit 설정 및 similarity의 동적 `_core` 조회, database의 canonicalizer 호출은 손대지 않았다.

## 검증

변경 전 전체 기준선은 2,701 passed / 기존 3 failed / 7 skipped. T2-A의 55개 characterization을 포함한 관련 149개가 변경 후 통과했다. canonical/isomeric, atom stereo/map, isotope/charge/aromaticity, invalid/blank/None, RDKit 예외 instance, dedup·DB rollback/provenance를 검증한다. 마지막 T9 전체 검증은 별도 기록한다. 기존 assertion을 수정하지 않았다.

## 의존성 및 보존 한계

새 방향은 `_core.canonicalize_smiles → chemistry.smiles → rdkit.Chem`이다. database는 여전히 `_core.canonicalize_smiles`를 동적으로 호출한다. **기존 database↔core의 함수 실행 의존성은 남는다.** 이를 순환 의존성 제거 완료로 해석하지 않는다.

기존 테스트의 `Chem.MolFromSmiles/MolToSmiles` method monkeypatch는 같은 RDKit module object를 통해 유지된다. 미제공 외부 코드의 `_core.Chem` 전체 재바인딩은 확인하지 못했으며 지원을 새로 보장하지 않는다. 실제 native 계산 정확도 검증은 하지 않았다.

## Fingerprint와 보류

두 파일은 조사한 CReM/standalone/Slurm/remote implementation source 목록에 포함되지 않는다. chemistry/combichem3d/*.py glob과 chemistry/smiles.py를 구별한다. native helper payload·저장 checksum/manifest·DB를 바꾸지 않았다. 새 파일을 포함한 package 전체 배포가 필요하다. 현재 source guard가 모든 전이 의존성을 포괄한다는 보증은 아니다.

FP generator 이동, similarity의 lookup 변경, DB direct-chemistry 전환은 보류한다. 공유 객체 재바인딩, reference/pose 선택과 fingerprint 대상 DB 변경을 첫 추출에 섞지 않는다. 다음 단계 T3을 자동 진행했다.
