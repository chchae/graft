# T2 재개 — Fingerprint 생성 정책과 순수 Tanimoto 구현 분리

작성일: 2026-11-15 (KST). main 9ec975f 위의 T6 재개·기존 사용자 미커밋 변경을 포함한 **현재 작업 트리**가 기준이다. 앞선 canonicalizer 분리는 유지했고 이번에는 남은 FP/similarity 경계를 검토·구현했다.

## 1. 실제 구현과 보존 인터페이스

### T2-C: 생성 설정 소유권

새 `chemistry/fingerprints.py::create_morgan_generator()`가 기존 `GetMorganGenerator(radius=2, fpSize=2048)` 호출을 그대로 소유한다. `_core.FP_GENERATOR`는 원래 위치에서 이 factory를 호출한다. module-level singleton을 새 leaf에 만들지 않았다.

그 결과 기존 core의 import/reload마다 새 generator 하나를 만들던 시점·횟수와 process-local 공유 binding을 유지한다. Morgan radius/bit 수 및 RDKit default를 바꾸지 않았고 includeChirality=False를 새 인자로 강제하지 않았다. 관찰한 options/info/bit vector가 원본과 같다. core의 기존 rdFingerprintGenerator import도 method monkeypatch 경계를 위해 보존했다.

**생성 정책 구현은 chemistry가 소유하지만 mutable compatibility binding의 소유 위치는 여전히 core다.** 이 둘을 혼동하지 않는다. wrapper alias로 같은 leaf singleton을 재사용하면 core reload 의미가 달라지므로 그 방법을 쓰지 않았다.

### T2-D: 순수 Tanimoto 구현

`chemistry.fingerprints.tanimoto_with_fingerprint(smiles_a, smiles_b, fingerprint)`는 RDKit parsing→invalid guard→두 fingerprint→float Tanimoto의 기존 구현을 그대로 소유한다. 필요한 fingerprint callable을 명시적으로 받으며 core/DB/config를 import하지 않는다.

기존 `chemistry.similarity.tanimoto(smiles_a, smiles_b) -> float`는 같은 public signature/module/name을 가진 compatibility wrapper다. **각 분자마다** `_core.FP_GENERATOR.GetFingerprint`를 다시 조회하는 lambda를 넘긴다. 첫 fingerprint 호출 중 generator 또는 similarity._core binding이 바뀌어도 두 번째 호출이 새 binding을 읽는 기존 동작을 유지한다. 한 번 캡처한 bound method로 바꾸지 않았다.

기존 similarity의 `_core`, Chem, DataStructs module attribute도 보존했다. RDKit method monkeypatch는 공유 module object에 적용된다. 미제공 외부 코드의 Chem/DataStructs 전체 module 재바인딩이나 function.__globals__ introspection 동등성까지 새로 보장하지 않는다.

## 2. 분자·예외·데이터 계약

- Canonical/isomeric SMILES, atom stereo/map, charge/isotope/aromaticity, molecule ID/dedup/pose ancestry는 변경하지 않았다. chemistry/smiles.py 및 DB 호출은 그대로다.
- Tanimoto는 a와 b를 각각 먼저 parse한 뒤 invalid 여부를 판단한다. invalid a와 None b의 TypeError 우선순위를 유지한다.
- Empty molecule 처리, bytes 입력, ValueError('Invalid SMILES'), RDKit/fingerprint/metric exception instance 전파를 유지한다. 새 정상화·tautomer/neutralization/strict input guard는 없다.
- Morgan chirality=False와 canonical stereo identity는 다른 계약이다. 관찰된 enantiomer/E-Z similarity=1을 dedup identity로 사용하지 않는다.
- Reference fingerprint/assignment는 기존 공유 generator를 조회한다. 동일 similarity tie는 기존 reference ID 순서로 선택하고 invalid/unassigned 및 이미 docking score가 있는 row 처리를 유지한다.

## 3. 의존성 변화와 남는 역참조

```text
_core.FP_GENERATOR 초기화 → chemistry.fingerprints.create_morgan_generator → RDKit
similarity.tanimoto (기존 facade)
  → chemistry.fingerprints.tanimoto_with_fingerprint
     → RDKit + caller가 제공한 fingerprint callable

database → _core.canonicalize_smiles → chemistry.smiles (기존 유지)
_core → database (기존 deferred imports 유지)
```

새 leaf는 단독 import/계산할 수 있다. **기존 similarity facade의 core 참조와 core↔database 논리 순환은 남는다.** 기존 override/patch 계약을 지키면서 구현 소유권을 분리한 것이며 전체 역의존 제거 완료로 표현하지 않는다. score diversity/report/combichem3d의 다른 generator를 이번 factory로 일괄 통합하지 않았다.

## 4. DB 조사 결과와 적용 보류

`database.project_smiles`, `import_crem_results`, novel_generated_smiles, insert_generated_candidates는 현재도 core canonicalizer를 동적으로 조회한다. 기존 55개 chemistry characterization에는 그 patch 경계, invalid/None dedup·NOT NULL 실패/rollback 및 provenance 보존이 있다.

최신 source의 project_smiles SQL에는 **ORDER BY id가 있다**. T2-A 당시의 무정렬 SQL 설명을 그대로 가정하지 않고 현재 구현을 기준으로 확인했으며 이 파일을 수정하지 않았다. source SHA256도 이번 시작값과 동일하다.

DB를 direct leaf import로 바꾸면 기존 core override 조회를 잃고 database.py의 Slurm source signature도 달라진다. 기존 작업 재개와 patch 계약을 보존한다는 증거가 없으므로 **DB caller 변경은 적용하지 않았다**. fingerprint 우회·allowlist·manifest 수정으로 이를 해결하지 않는다. T3/T4/T7 보류 패치도 적용하지 않았다.

## 5. 변경 전 보존 테스트와 원본 관찰

먼저 `test_fingerprint_binding_contracts.py` 11개를 기존 구현에 실행했다: core import/reload의 정확한 factory 인자·생성 횟수, 생성 실패 instance 전파, 두 fingerprint 사이의 generator/core module 교체, parse/fingerprint/metric 순서·예외, 실제 임시 SQLite reference tie/identity 보존을 확인한다.

추가로 **변경 전 source snapshot을 별도 Python에서 import**하여 Morgan bit vector 10개와 similarity 값/예외 19개를 실제 실행하고 `tests/data/refactoring/fingerprint-current.json`에 고정했다. 단순히 새 구현과 새 wrapper를 서로 비교한 것이 아니다. 원본 module 경로와 실행 기록을 evidence에 남겼다.

새 leaf 테스트 31개는 generator 없는 import·core/DB/config 없는 계산, fresh factory 객체, 원본 info/2048 bit 전체 on-bit 위치, stereo/charge/isotope/map/aromatic·invalid/empty/None/bytes의 원본 결과와 legacy/new API 동등성을 검증한다. **이번 신규 case는 총 42개**이며 기존 테스트·assertion/xfail/skip은 바꾸지 않았다.

## 6. 검증 결과

| 검증 | 결과 |
|---|---|
| 변경 전 전체 baseline | 2,777 passed / 기존 3 failed / 7 skipped |
| 생성 factory 변경 전후 관련 | 240 passed → 240 passed, node 변화 0개 |
| Tanimoto 변경 전후 관련 | 131 passed → 131 passed, node 변화 0개 |
| 전체 chemistry leaf/기존 계약 묶음 | 97 passed |
| 최종 전체 | **2,819 passed / 동일 3 failed / 7 skipped** |
| 기존 2,787개 node 비교 | outcome/message/assertion 변화 0개; 새 42개 모두 passed |
| compileall / package·CLI·chemistry import | 성공 |
| graft/driver/generate/glide CLI help | stdout/stderr byte 동일 |
| 원본 알고리즘 AST | factory의 원래 call을 되넣으면 core 전체 AST 일치; fingerprint callback을 원래 조회로 복원하면 Tanimoto body AST 일치 |
| 보호 source/native 40개 / DB·config·canonicalizer | 시작 snapshot과 SHA256 동일 |
| wheel | 새 chemistry/fingerprints.py 포함 확인 |

baseline은 84.82초, 최종은 85.27초다. 성능 비교/최적화 결과로 해석하지 않는다. 기존 multi-threaded fork warning과 Torch 부재 7 skip을 유지했다. 세 실패 node는 T0/T9/T6 재개와 같다:

```text
tests/test_combichem_3d.py::test_real_probes_pass_but_actual_fragment_combination_collides_with_itself
tests/test_md_progress_summary.py::test_driver_pending_line_includes_progress_and_elapsed_even_on_worker_error[0]
tests/test_sequential_driver.py::test_pending_log_uses_persisted_iteration_stage_elapsed[md-gbsa-MD-GBSA still pending for compound 624598]
```

비교에서는 memory address·격리 temp root·pytest fixture header만 정규화했다. assertion/결과 값·예외 message를 완화하지 않았다. raw XML/log와 comparison JSON을 함께 보관한다.

## 7. Fingerprint·운영 보호와 미검증

변경 runtime은 `_core.py`, `chemistry/similarity.py`, 새 `chemistry/fingerprints.py`로 제한했다. 이 파일들은 조사한 CReM/standalone/Slurm/remote source hash 목록의 직접 대상 밖이다. chemistry/combichem3d glob과 새 chemistry leaf를 구별한다. 현재 guard가 모든 전이 dependency를 포괄한다는 주장은 하지 않는다.

기존 native helper 및 source guard/allowlist, DB·manifest·저장 checksum/JobId/결과는 수정하지 않았다. 실제 native 계산·SSH/Slurm/JobServer 작업을 제출/회수하거나 helper를 업로드하지 않았다. 별도 snapshot `/tmp/graft-t2-restart-5wdgrci4/project`와 venv/TMPDIR/registry에서 검증했으며 운영 설치는 재설치하지 않았다.

새 leaf를 포함한 package 단위 배포가 필요하다. 실제 원격 Python/RDKit 환경, 모든 chemical edge case·RDKit 버전, live job restart 및 native scientific accuracy는 미검증이다. 기본값/chirality/identity를 바꾸는 버그 수정·성능 최적화는 이 작업에 포함하지 않았다.

## 8. 리뷰·다음 범위

[두 단계 패치](t2-restart/patches/)를 별도 임시 디렉터리에 replay하여 최종 source/tests와 byte 일치를 확인했다. C→D 순서를 고려해 검토/되돌릴 수 있다. 기존 사용자 변경·T6 재개는 시작 snapshot에 포함해 보존했다.

T2의 안전하게 수행 가능한 생성 정책·계산 구현 분리는 적용한다. DB caller 전환과 legacy mutable binding 제거, 전체 core↔DB 순환 해소는 별도 호환성·배포 검증이 필요한 보류 범위다. T3/T4/T7로 임의 확대하지 않는다.

[전후 node 비교](t2-restart/comparison.json), [소스 검사](t2-restart/source-audit.json), [원본 관찰](t2-restart/original-chemistry-observation.log), [최종 전체 로그](t2-restart/final-full.log)를 남겼다.

## 9. 최종 반영 확인

새 fingerprints module을 먼저 배치한 뒤 legacy core/similarity를 원자적으로 교체했다. 기존 파일 SHA256을 반영 전후 확인했으며, 기존 입력 391개 중 의도한 source 2개와 문서 2개만 변경하고 나머지 387개를 보존했다. T6 재개·다른 사용자 변경도 유지했다. source/tests는 전체 검증 후보와 byte 동일하며, 원본 경로에서도 compileall·package/CLI import·legacy/pure Tanimoto smoke 및 diff whitespace 검사를 통과했다. [반영 기록](t2-restart/applied-worktree.json)을 남겼다.
