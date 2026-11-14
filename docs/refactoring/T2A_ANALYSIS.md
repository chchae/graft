# T2-A — 순수 Chemistry 의존성 분석 및 보존 테스트

작성일: 2026-11-14 (KST)  
범위: **분석 및 변경 전 characterization test 확보만 수행했다. 런타임 소스 이동·수정, DB/manifest 수정, fingerprint 변경은 없다. T2-B는 시작하지 않았다.**

## 1. 요약과 권장 첫 단계

가장 먼저 분리할 함수는 **`_core.canonicalize_smiles(smiles)` 하나**다. 현재 `_core.py:687`의 구현을 향후 `src/graft/chemistry/smiles.py`로 옮기고, `_core`의 동일 이름·시그니처를 forwarding wrapper로 남기는 작은 단계부터 권고한다. **그 첫 단계에서는 FP_GENERATOR, similarity, database의 호출 경로를 함께 바꾸지 않는다.**

이렇게 제한하는 이유는 다음과 같다.

- canonicalizer는 직접 SQL·파일·외부 프로세스를 사용하지 않는 작은 RDKit 함수이며, 출력·입력 경계·예외 전파를 구체적으로 고정할 수 있다.
- FP_GENERATOR는 module import 시 만들어지는 공유 C++ 객체다. similarity가 `_core.FP_GENERATOR`를 매번 조회하므로, 객체의 배치만 바꿔도 재바인딩/monkeypatch 의미가 달라질 수 있다.
- DB 함수는 SQL transaction·identity·provenance를 함께 처리하며 `database.py` 자체가 **Slurm source signature 대상**이다. helper 추출과 DB caller 전환을 한 번에 묶으면 fingerprint 및 복구 위험이 커진다.
- compatibility wrapper만 먼저 도입하면 DB→core 및 core→DB의 논리 순환은 **그대로 남는다**. 이번 첫 단계만으로 순환 의존성이 해소된다고 주장하지 않는다. 새 chemistry leaf를 독립적으로 import·검증할 수 있는 구현 소유권 경계를 만드는 제한된 단계다.

추가 테스트는 현재 함수를 직접 호출하는 **55개 case**다. 기존 테스트·기대값은 바꾸지 않았다. 관찰된 동작은 보존 검증의 기준이지, 이상한 동작을 새 과학적 요구사항으로 승인한 것이 아니다.

**검증 결과:** 기존 관련 테스트 246개 통과, 새 characterization 55개 통과. 전체 **2,701 passed / 기존 동일 3 failed / 7 skipped**. T1의 기존 2,656개 case outcome과 실패 내용은 동일하며 신규 실패는 없다. [검증 기록](t2a/verification.json), [관련 테스트 로그](t2a/existing-related.log), [전체 테스트 로그](t2a/pytest-after.log)를 남겼다.

## 2. 조사 범위와 증거

- 기준은 T1을 적용한 현재 작업 트리다. 기존 미커밋/미추적 source와 test, 설정·문서를 보존한 snapshot 및 SHA256을 확보했다.
- `_core.py`, `database.py`, `chemistry/similarity.py`, 실제 caller인 생성/합성 가능성 stage, source signature 생성부, 기존 테스트와 제공된 example/script를 조사했다. 저장소 밖의 미제공 사용자는 확인하지 못했다.
- 기존 코드와 같은 Python 3.15.2 / RDKit 2026.09.1 / NumPy 2.5.4 / pytest 9.2.0을 사용했다. 별도 복사본 `/tmp/graft-t2a-9ro3ec2q/project`와 venv에 복사본만 editable 설치하고, 운영 설치나 의존성을 변경하지 않았다.
- 동작 확인은 현재 함수 호출, synthetic 분자와 임시 SQLite, RDKit 함수/전역 binding의 테스트용 monkeypatch로 수행했다. JobServer·native 계산을 제출하거나 운영 DB·manifest를 읽어 재작성하지 않았다.
- static import/call 관계와 실제 관찰을 구분한다. 아래의 native 재개 분석은 source hash 입력 목록과 의존성에 대한 분석이며, 실제 진행 중 작업을 재시작한 검증이 아니다.

## 3. 현재 의존성과 초기화

### 3.1 현재 호출 방향

```text
chemistry/similarity.py
  └─ import graft._core
       └─ FP_GENERATOR  ← tanimoto가 호출할 때마다 조회

database.py
  └─ import graft._core
       ├─ canonicalize_smiles ← project_smiles / import_crem_results 등
       ├─ open_sqlite         ← connect / claim
       ├─ configure_project   ← initialize
       ├─ set_compound_state / find_iteration_for_state
       └─ create_sqlite_schema ← growth DB 초기화

_core.py
  └─ 함수 내부에서 database를 import
       └─ schema 보완 / migration / reference cache / claim·결과 처리

stages/crem_generate.py
  ├─ database.project_smiles → 기존 canonical identity 목록
  └─ database.import_crem_results → 신규 identity·pose·완료 상태 원자적 저장
```

`database → _core`는 top-level import지만 `_core → database`는 주로 함수 내부의 지연 import다. 현재 이 구조가 **module 초기화 순환 오류를 일으킨다고 재현된 것은 아니다.** 함수 실행 단계의 양방향 의존성 및 core 구현 집중이 문제다.

특히 database가 canonicalizer를 호출하는 지점은 대상 두 함수 외에도 `novel_generated_smiles()`와 `insert_generated_candidates()`에 있다. 두 대상 함수만 direct chemistry import로 바꿔도 다른 canonicalizer 및 DB 기반 primitive 때문에 `database → _core`는 남는다.

### 3.2 모듈 초기화와 숨겨진 상태

| 대상 | module import 시 / 함수 호출 시 |
|---|---|
| `_core` | RDKit 등의 module import와 `GetMorganGenerator(radius=2, fpSize=2048)` 실행. 정상 import는 Python module cache를 재사용하며, 명시적 reload에는 새 generator가 만들어질 수 있음 |
| `FP_GENERATOR` | process-local 공유 C++ `FingerprintGenerator64` 객체. `64`는 `GetFingerprint()` 출력이 64 bit라는 뜻이 아니며 실제 출력은 2,048 bit |
| `similarity` | RDKit과 `_core`를 import. 자체 generator를 만들지 않고 매 호출 `_core` binding 사용 |
| `database` | `_core`, 표준 라이브러리와 state enum 등을 import. source에는 연결이 함수 안에 있음. `connect()` 호출은 WAL/busy timeout/foreign-key 준비를 포함 |
| canonicalizer | 직접 DB/파일/프로세스 접근은 없지만 RDKit 파싱 실패는 RDKit stderr 진단을 낼 수 있음. “모든 관찰 가능한 부작용이 없는 함수”로 과장하지 않음 |

새 Python 프로세스에서 `sqlite3.connect`, `subprocess.run`, `subprocess.Popen`을 호출 시 실패하도록 막고 세 모듈을 import했다. import는 성공했으며 이 세 I/O 경계 호출은 **0회**였다. 이는 모든 OS 파일 읽기/라이브러리 초기화가 없다는 검증은 아니다. module-level FP 설정은 radius 2 / 2048 bit / chirality False로 관찰됐다.

## 4. 대상별 구현·입출력 계약·caller

### 4.1 `_core.canonicalize_smiles`

현재 signature: `canonicalize_smiles(smiles: str) -> str | None`.

구현의 정확한 순서:

1. 문자열이 아니거나 `smiles.strip()`이 비어 있으면 RDKit 호출 없이 `None`.
2. **원래 입력 문자열**을 `Chem.MolFromSmiles(smiles)`에 전달한다. strip 결과를 전달하지 않는다.
3. parser 반환값이 `None` 또는 atom 0개 molecule이면 `None`.
4. `Chem.MolToSmiles(mol, canonical=True, isomericSmiles=True)` 결과를 반환한다.
5. parser/writer가 실제 예외를 던지면 catch하지 않고 그대로 전파한다.

| 입력/경계 | 현재 관찰 |
|---|---|
| `OCC` | `CCO` |
| ` CCO `, `CCO label` | `CCO`; 현재 RDKit의 whitespace/name parsing 동작을 그대로 사용 |
| `C1=CC=CC=C1` | `c1ccccc1` |
| `C[C@H](O)F` / `C[C@@H](O)F` | 서로 다른 isomeric 문자열 유지 |
| `F/C=C/F` / `F/C=C\F` | double-bond stereo 구분 유지 |
| `[NH4+]`, `[13CH3]CO`, `[2H]OC` | 전하·동위원소 보존 |
| `[H]OC` | `CO`; ordinary explicit H는 현재 RDKit parser의 H 제거 규칙을 따름 |
| `[CH3:7][OH:9]` | atom map label 유지 |
| `[Na+].CC` | `CC.[Na+]`; component를 제거하거나 neutralize하지 않음 |
| `None`, `b'CCO'`, int/list, empty/whitespace | `None` |
| syntax 오류 / kekulization·valence 실패 | 관찰된 fixture는 `None`; RDKit 진단 출력 가능 |
| RDKit 호출이 예외를 던지는 경우 | 주입한 RuntimeError instance 그대로 전파. 실제 모든 RDKit 실패가 이 예외를 던진다는 뜻은 아님 |

내부 직접 caller는 `_core.write_training_files()`, `get_existing_canonical_smiles()`다. 외부 module caller는 database 네 함수의 다섯 호출 지점 및 `stages.synthetic_feasibility`의 `_read_results`, `process_iteration`, `import_results`다. 제공된 example/script에서 추가 직접 caller는 발견하지 못했지만 import 가능한 기존 이름이므로 외부 사용 가능성을 보존한다.

`chemistry/combichem3d/history.py::canonical_smiles(mol)`은 대체 함수가 아니다. 이 함수는 molecule을 복사/RemoveHs하고 **atom map을 지운다**. `_core`의 string parser 계약과 다르며 그 파일은 fingerprint 대상이다. 이번 목적을 위해 두 구현을 합치지 않는다.

### 4.2 `_core.FP_GENERATOR`

생성 위치는 `_core.py:652`이며 factory에 명시한 인자는 `radius=2`, `fpSize=2048`이다. 현재 RDKit options에서 `includeChirality=False`가 관찰된다. 기본값을 추측해서 새로 정한 값이 아니다.

- `GetFingerprint(rdkit_mol)`은 길이 2048의 bit vector를 만든다. CCO의 on bits는 현재 환경에서 `(80, 222, 294, 807, 1057, 1410)`이다.
- info string에는 `countSimulation=0`, `bitsPerFeature=1`, `includeChirality=0`, Morgan radius 2, ring membership/bond-type 사용 등이 나타난다. 전체 info는 검증 기록에 남기되 아직 조사하지 않은 모든 RDKit 옵션을 새 지원 API로 규정하지 않는다.
- `_core.get_reference_compounds()`가 reference fingerprint를 만들고, `assign_reference_compounds()`가 query fingerprint와 BulkTanimotoSimilarity를 사용한다. **reference 선택과 pose mapping에 영향을 주는 공유 객체**다.
- `chemistry.similarity.tanimoto()`도 이 객체를 조회한다. 다른 selection/report/3D evaluator가 생성한 비슷한 Morgan 객체까지 이번 대상에 포함하지 않는다.
- enantiomer, E/Z 및 isotope가 canonical identity에서 구분되어도 fingerprint similarity가 1일 수 있다. 관찰한 쌍에서 그러했으며 **Tanimoto 1을 molecule identity 동일성으로 사용하면 안 된다.** 모든 동위원소/분자 조합에 대한 일반적 물리 보증은 아니다.
- 기존 테스트에서 이 이름의 patch는 발견하지 못했다. T2-A의 추가 테스트는 현재 binding을 spy generator로 잠시 바꿔, similarity가 실제로 이 binding을 두 번 읽는다는 사실을 고정한다. 이것은 기존 동작의 관찰이며 외부 모든 monkeypatch 형태를 지원한다는 새 요구가 아니다.

### 4.3 `chemistry/similarity.py::tanimoto`

Signature: `tanimoto(smiles_a: str, smiles_b: str) -> float`.

1. a를 `MolFromSmiles`, b를 `MolFromSmiles`로 **각각 먼저 파싱**한다.
2. 두 결과 중 `None`이 있으면 `ValueError("Invalid SMILES")`.
3. 각각 `_core.FP_GENERATOR.GetFingerprint()`를 호출한다.
4. `float(DataStructs.TanimotoSimilarity(fa, fb))`를 반환한다. toolkit 예외를 catch하지 않는다.

| 입력 | 관찰된 반환/예외 |
|---|---|
| CCO / OCC | 1.0 |
| CCO / CCN, 반대 순서 | 정확히 Python float `1/3` |
| 위 enantiomer 쌍 / E-Z 쌍 / `[13CH3]CO`와 CCO | 각각 1.0 |
| `[NH4+]` / N | 0.0 |
| aromatic/Kekulé benzene 쌍 | 1.0 |
| `''` / `''`, `''` / CCO | **0.0**. empty molecule을 canonicalizer처럼 미리 거부하지 않음 |
| `' '` 또는 invalid string / CCO | `ValueError("Invalid SMILES")` |
| `None` / CCO, CCO / None, int / CCO | 현재 RDKit converter의 TypeError. platform-dependent 전체 오류 문구는 고정하지 않음 |
| invalid string / None | b를 파싱할 때 TypeError. a의 invalid 결과를 곧바로 ValueError로 바꾸면 관찰 순서가 달라짐 |
| `b'CCO'` / CCO | 1.0; type hint가 str이어도 현재 별도 runtime 타입 검사를 하지 않음 |

기존 저장소에서 이 helper의 직접 실행 caller/test를 찾지 못했다. 현재 docking reference 계산은 core 안에서 직접 bulk similarity를 사용한다. 그렇다고 공개적으로 import 가능한 helper를 제거하거나 예외/빈 입력 계약을 임의 변경하지 않는다.

### 4.4 `database.project_smiles(path)`

- `connect(path)`로 DB를 열고 `SELECT id,smiles FROM compound`의 각 row에 `_core.canonicalize_smiles()`를 적용해 **list of `(id, str | None)`**를 반환한다.
- iteration/state/finite-score 필터가 없고 중복 row 또는 `None` 결과를 제거하지 않는다. 기존 raw SMILES·ID·score·state를 canonical 값으로 UPDATE하지 않는다.
- `ORDER BY`가 없다. 테스트 fixture에서는 rowid/ID 순서가 관찰됐지만 모든 schema/query plan에 대한 새 정렬 보장을 만들지 않는다.
- 직접 caller는 `stages.crem_generate.run_stage()`다. 기존 identity를 생성의 제외 목록으로 넘긴다.
- 명시적 read-only URI 함수는 아니다. `connect()`의 WAL/pragma 준비 등 연결 부작용과 SQL/경로 오류, canonicalizer 예외는 별개다. “파일 생성/쓰기 가능성이 전혀 없다”고 새 계약을 만들지 않는다.
- 새 테스트는 ID, 중복, invalid의 `None`, 기존 compound row 불변을 확인한다. 현재 `_core` binding의 monkeypatch를 조회하는 사실도 관찰 테스트에 포함했다.

### 4.5 `database.import_crem_results(path, iteration, records, stop_reason)`

SQL·JSON·identity·provenance·완료 상태를 함께 다루는 **I/O 함수**이며 pure chemistry 함수가 아니다.

- stop reason은 `target_count` 또는 `exhausted`만 허용하고 다른 값은 ValueError다.
- `BEGIN IMMEDIATE` transaction 안에서 frozen plan을 읽는다. plan이 없으면 RuntimeError다. DONE이면 저장된 population의 readiness를 확인하고 저장 count를 반환하며 새 records를 소비하지 않는다.
- 기존 project 전체 SMILES를 canonicalize한 set을 만든다. 새 row는 history iteration 확인 → canonicalize → 중복 확인 → target count cap → 필요한 parent 검증 → insert 순서다. 이 검증 순서를 helper 이동과 함께 바꾸지 않는다.
- string canonical identity로 전체 project 및 같은 batch 중복을 제거한다. **fingerprint나 Tanimoto로 중복을 판단하지 않는다.** enantiomer·isotope·charge identity를 합치지 않는다.
- INSERT된 SQLite id를 `CMPID_<id>`로 이름에 기록하고 molblock/history/properties를 `crem_compound_pose`에 저장한다. parent-specific protocol은 바로 전 iteration의 frozen parent와 generation_parent_id를 검증한다.
- 성공하면 완료 상태·삽입 count·stop reason을 저장하고 count를 반환한다. DONE replay는 기존 count를 반환한다. 예외는 transaction을 rollback한다.
- upstream의 검증된 iterable/JSON-serializable record를 전제로 하는 코드이며 모든 필드 타입을 자체적으로 정규화·검증하는 API가 아니다. Molblock을 새 canonical SMILES로 다시 만들거나 원자/좌표를 고치지 않는다.

기존 `test_crem_pipeline.py`에는 parent 오류 rollback, 부분 import 중단 및 재시작, scientific settings 변경 거부 등이 있다. `_core` helper 하나를 고치는 작업에서도 canonical output이 달라지면 저장/중복·ID allocation 결과가 달라지므로 새로운 direct boundary test를 추가했다.

**관찰된 별도 검토 사항 — None identity:**

- 기존 DB canonical set에 `None`이 없으면 새 invalid/None SMILES는 NOT NULL 제약에서 `sqlite3.IntegrityError`를 일으키며 먼저 넣은 valid row까지 rollback된다.
- 기존 invalid SMILES가 `None`으로 canonicalize되어 set에 있으면 새 invalid/None 입력을 중복으로 건너뛰고 valid row는 commit될 수 있다.
- 임시 DB에서 두 경우를 각각 재현했다. 이 behavior를 유지하는 테스트는 리팩토링 중 조용한 변화 탐지용이며, invalid scientific input을 정당한 정책으로 승인한 것이 아니다. 향후 validation bug/hardening 여부는 별도 조사·수정 작업으로 분리한다. 이번 단계에서는 수정하지 않았다.

## 5. 공개 인터페이스·동적 참조·monkeypatch

| 경계 | 현재 조사와 보존 방안 |
|---|---|
| `_core.canonicalize_smiles` | import 가능한 기존 이름·signature, keyword `smiles`, 반환/예외를 유지. wrapper를 남겨 호출자 수정 없이 동일 동작을 검증 |
| `_core.FP_GENERATOR` | 같은 기존 객체와 현재 생성 시점을 첫 단계에서 유지. similarity의 동적 `_core` 조회도 그대로 둠 |
| `similarity.tanimoto` | path/name·signature·정확한 float·빈 입력·예외 순서를 유지. 첫 단계에서는 구현을 손대지 않음 |
| DB 두 함수 | 기존 path/name·signature·transaction·결과 및 `_core` 조회를 첫 단계에서 유지. 이 참조를 직접 leaf import로 바꾸는 것은 별도 변경 |
| RDKit method patch | parser/writer method를 주입 실패로 바꿨을 때 현재 예외 전파를 확인. 새 leaf가 같은 `rdkit.Chem` module object를 참조하면 이 method patch는 유지 가능 |
| 임의 module 재바인딩 | `_core.Chem` 전체를 다른 객체로 교체하는 외부 코드는 발견하지 못함. 그런 미제공 확장까지 검증/지원했다고 주장하지 않음 |
| 기존 테스트 patch | 현재 `database.import_crem_results`를 wrapper로 patch하여 import 중단·재시작을 시험하는 두 곳이 있음. 새 테스트 이전에 canonicalizer/FP_GENERATOR를 patch하는 기존 test는 찾지 못했음 |
| Native/동적 helper | 현재 내장/native Python script가 이 대상 symbol을 문자열 lookup하는 경로는 찾지 못함. 원격 Graft process는 package 전체 import가 가능하므로 새 module 배포 여부는 후속 단계에서 확인해야 함 |

새 보존 테스트가 일부 현재 binding을 spy/주입 실패로 확인한다는 점과, 과거 실제 caller가 그러한 patch를 사용했다는 주장은 구분한다. 현재 범위에서는 이 관찰 seam을 유지하는 forwarding만 제안하여 모호한 호환성을 임의로 줄이지 않는다.

## 6. 화학적 동등성 검증 범위

| 항목 | 기존 테스트의 충분성 조사 | 이번 추가 검증 |
|---|---|---|
| canonical/isomeric SMILES | generated selection/combichem/descriptor 등의 간접 검증 있음. core helper 직접 경계는 부족 | canonical 예제와 atom·double-bond stereo literal 출력 |
| Atom stereo | combichem assembly/retained core/parent pose·ring stereo 테스트 있음 | core string canonicalizer의 enantiomer 구분 및 DB에서 별도 identity 유지 |
| charge/isotope | assembly·generation element·isotopic hydrogen 테스트 있음 | NH4+, 13C, 2H·ordinary H·salt component·map label 보존 |
| aromaticity | ring/descriptor·assembly를 통한 간접 검증 | Kekulé → aromatic canonical, aromatic/Kekulé similarity 동일 |
| invalid/empty/None | SA/descriptor의 별도 정책 및 generation invalid skip은 있음. 대상 helper와 동일한 계약은 아님 | core의 None/blank/non-string guard, parse/sanitization failure, similarity의 다른 빈/None 결과 |
| RDKit 예외 | 화학 search/force-field 예외 tests는 있으나 canonicalizer passthrough와 다름 | parser/writer의 주입 예외 instance 전파, import 중 canonicalizer 예외 rollback |
| radius/bit/chirality | SPEC에 radius2/2048, score diversity/reference 등의 간접 검증 있음 | options와 관찰 bit vector, enantiomer/E-Z similarity 1이라는 현재 정책 |
| Tanimoto | reference grouping/selection의 간접 검증 있음 | helper의 정확한 1.0, 0.0, 1/3, parse 순서 및 global generator lookup |
| 중복/identity | DB uniqueness·generation·CReM 재시작 tests 있음 | raw 기존 row 보존, project 목록의 ID/None/중복, stereo/isotope/charge와 intra-batch dedup 구분 |
| 기존 molecule/pose | parent provenance·CReM identity 검증 있음 | CMPID, 원래 scores/state/failure, molblock·history·properties, completed replay 불변 |

추가 파일은 `tests/test_chemistry_contracts.py` 하나이며 **55개 case**다. 새 정규화 정책, neutralization, tautomer 변경, 엄격한 타입 검사, arbitrary tolerance 또는 Chirality=True 변경은 추가하지 않았다. float 기대값은 관찰한 Python 값 그대로이고 canonical/bit-vector 기대는 현재 RDKit 환경에 한정한 baseline이다.

검증하지 못한 범위: 모든 가능한 molecular stereochemistry/CXSMILES/금속·tautomer, 모든 RDKit 버전의 byte-identical canonical/bit 값, 실제 suite I/O 및 docking/affinity/native scientific correctness. 기존 ring/stereo tests의 통과를 모든 canonicalization edge case의 완전성으로 일반화하지 않는다.

## 7. Source fingerprint와 진행 중 작업 호환성

| 파일/범위 | 현재 포함 여부 및 주의 |
|---|---|
| `_core.py`, `chemistry/similarity.py` | 조사한 CReM/standalone/Slurm source hash 목록의 직접 대상 아님 |
| `database.py` | **`combichem_slurm.signature()`의 명시 source 대상**. pure helper caller로 바꾸는 한 줄도 saved signature를 바꿀 수 있음 |
| `chemistry/combichem3d/*.py` | CReM 및 standalone scientific source hash 대상. `history.canonical_smiles` 수정·통합을 피해야 함 |
| `crem_generate.py`, `combichem_3d.py`, `combichem_product.py`, buffer/remote/Slurm helper | 각 생성 protocol/source signature 대상. 이번에 수정하지 않음 |
| 제안하는 `chemistry/smiles.py` | 아직 존재하지 않으며 만들지 않았음. 현재 glob의 직접 대상 밖에 놓이는 leaf지만 향후 규칙 전체를 다시 검토해야 함 |
| native/Amber/MD/reference cache helper | checksum·protocol/원자 mapping 계약 유지. 수정/업로드 없음 |

**이번 T2-A의 영향은 0:** 모든 기존 `src/` Python 파일의 SHA256을 작업 시작값과 대조한다. 테스트·분석 문서는 현 source signature 대상이 아니다. 따라서 이번 작업 때문에 source mismatch를 새로 만들지 않는다. 실제 진행 중인 job의 native 상태를 확인하거나 재개를 실행한 검증은 아니다.

후속 분리의 조건:

1. first wrapper/leaf 변경은 현재 source 목록에서 `_core`·새 leaf만 대상으로 제한한다. DB나 fingerprint 대상 파일의 caller까지 함께 바꾸지 않는다.
2. 현재 목록이 일부 core helper를 hash하지 않는 것은 기존 coverage 특성이다. 새 leaf를 그 밖에 두는 것을 계산 정책 변경을 숨기는 방법으로 사용하지 않는다. fingerprint coverage를 확장하려면 별도의 versioned/new-work protocol·legacy validation 설계가 필요하며 이 작업에서 임의로 추가하지 않는다.
3. 새 module은 모든 local/SSH/shared-code package 경로에서 import 가능해야 한다. 기존 standalone native helper에 Graft import를 추가하지 않는다. 실제 원격 package 배포 검증은 미완료다.
4. DB source를 직접 바꾸는 후속 단계는 저장된 Slurm signature와 미완료 작업 호환을 먼저 검토하고 필요 시 배포를 보류한다. “계산값은 같으니 checksum을 무시해도 된다”는 처리는 금지한다.
5. old/new wrapper의 결과·예외·동적 binding·입출력을 검증하고, 운영 editable installation에 중간 상태를 노출하지 않는다. copy-only 검사에서 필요한 원격 검증은 별도로 수행한다.
6. 어떠한 기존 manifest/DB 변경, source hash 우회, 무검증 allowlist 또는 live/unknown job reset도 하지 않는다.

## 8. 후속 리팩토링 계획 — 아직 구현하지 않음

### 8.1 첫 순수 함수와 정확한 구현 위치

- 이동할 기존 구현: `src/graft/_core.py`의 `canonicalize_smiles` 함수 전체. 검증·RDKit call 인자·실행 순서·예외 처리를 그대로 유지한다.
- 구현 소유 위치: **새 `src/graft/chemistry/smiles.py::canonicalize_smiles`**. 의존은 `rdkit.Chem`만으로 제한하고 `_core`, database, config, stage를 import하지 않는다.
- 첫 변경에서 FP 생성·similarity·DB I/O는 옮기지 않는다. 새 클래스/registry/protocol adapter를 도입하지 않는다.

### 8.2 `_core` 호환 인터페이스

단순 alias는 함수 `__module__` 등 introspection 소유자를 바꿀 수 있으므로 일반 forwarding wrapper를 권고한다. 아래는 **계획용 예시이며 이번 소스에 반영하지 않았다.**

```python
def canonicalize_smiles(smiles: str) -> str | None:
    from graft.chemistry.smiles import canonicalize_smiles as _canonicalize
    return _canonicalize(smiles)
```

이 wrapper는 기존 이름/keyword/annotation/예외 경계를 유지하며 변환 결과에 새 검증을 더하지 않는다. DB와 synthetic stage는 첫 단계에서 기존 `_core` binding을 계속 사용하여 현재 lookup/monkeypatch seam을 유지한다. canonical implementation이 분리되어도 `_core.Chem`은 다른 기능이 사용하므로 제거하지 않는다.

### 8.3 새 의존 방향과 남는 순환

```text
현재:
  database ──> _core ──(함수 내부)──> database
  similarity ──> _core.FP_GENERATOR

첫 분리 후 권고:
  database ──> _core wrapper ──(lazy)──> chemistry.smiles ──> rdkit.Chem
  _core ──(기존 지연 경로)──> database
  similarity ──(기존 그대로)──> _core.FP_GENERATOR
```

`chemistry.smiles`는 core/DB에 의존하지 않는 leaf가 된다. **core↔database 순환은 이 단계로 제거되지 않는다.** 낮은 계층의 단독 실행·검증 가능한 경계 확보가 첫 성과이며, 전체 cycle 해소는 connection/schema/state 구현 소유권과 DB source fingerprint·호환성을 다루는 별도 단계가 필요하다. DB direct leaf import로 전환하더라도 나머지 DB primitive의 core 참조 때문에 즉시 cycle이 사라지지 않는다.

### 8.4 변경 예정 파일

| 첫 구현 단계의 파일 | 예정 변경 |
|---|---|
| `src/graft/chemistry/smiles.py` (신규) | canonicalize_smiles의 동일 구현, RDKit-only leaf |
| `src/graft/_core.py` | 기존 구현 대신 동일 시그니처 forwarding만 유지. FP 및 다른 함수 변경 없음 |
| `tests/test_chemistry_contracts.py` | 현재 55개 기대는 유지. 새 leaf를 직접 import한 결과/예외와 wrapper의 동등성 및 fresh-process import 경계를 추가 검증 |

`database.py`, `chemistry/similarity.py`, `chemistry/combichem3d/`, native helper, 설정/DB/manifest는 첫 분리의 변경 대상에서 제외한다. FP_GENERATOR 이동과 DB caller 전환은 이번 계획의 바로 다음 patch에 자동 포함하지 않는다.

### 8.5 권장 순서와 동등성 검증

1. T2-A 결과·관찰 seam·fingerprint 제약을 검토하고 **T2-B의 별도 승인**을 받는다.
2. 같은 환경의 isolated copy에서 현재 55개·기존 관련·known baseline 실패 결과를 확보한다.
3. canonical implementation 하나와 wrapper만 변경한다. 새 leaf import가 `_core`/database/FP 초기화·외부 I/O를 유입하지 않는지 새 프로세스에서 검사한다.
4. 같은 corpus를 이전 구현과 새 leaf/wrapper에 넣어 exact canonical string/None, 예외 type 및 주입 exception identity, 동적 monkeypatch, FP bits/options·Tanimoto를 비교한다. toolkit version을 바꾸거나 bit/float 오차를 새로 허용하지 않는다.
5. 같은 synthetic DB/records에서 project 목록·CMPID·scores/state/failure·dedup·pose/provenance·rollback·DONE replay가 동일한지 확인한다. None 관찰 사항을 조용히 “수정”하지 않는다.
6. 공개 signature/module entry/CLI 출력과 package import 가능성, generated helper bytes, 관련 source fingerprint, 전체 node outcome을 비교한다.
7. 기존 3건 외 새 실패, 원래 실패의 다른 원인, skip 증가, 결과/선정/ID 차이가 있으면 다음 단계로 진행하지 않는다. native scientific correctness는 별도 검증 상태로 보고한다.

### 8.6 주요 regression 위험

- Canonical와 similarity에 공통 입력 검증을 강제하여 None/empty/bytes/exception 순서를 바꾸는 위험.
- default RDKit writer 옵션·atom map·ordinary/isotopic H·aromaticity·stereo를 바꾸어 DB identity나 duplicate membership이 달라지는 위험.
- “같은 fingerprint니까 같은 분자”로 판단해 enantiomer/isotope row를 합치는 위험.
- FP alias 이동/재생성으로 동일 객체·동적 `_core` binding·reference tie/pose selection이 달라지는 위험.
- wrapper를 없애거나 DB가 leaf를 바로 import해 기존 lookup/patch/introspection 경계를 바꾸는 위험.
- `database.py` 또는 scientific source 변경으로 saved source signature가 깨지는 위험.
- 새 module의 원격/shared installation 누락 또는 unsupported private Chem module 재바인딩과의 차이. 제공된 caller만으로 모든 외부 확장을 검증할 수 없음.

## 9. 이번 테스트 결과와 변경 파일

| 실행 | 결과와 범위 |
|---|---|
| 기존 관련 8개 test 파일 | **246 passed**, 207 warnings, 25.39초, exit 0 |
| `tests/test_chemistry_contracts.py` | **55 passed**, 0.21초, exit 0 |
| 새 테스트 추가 후 전체 suite | **2,701 passed, 3 failed, 7 skipped**, 330 warnings, 86.57초, exit 1 |
| T1의 2,656개 case와 대조 | 기존 outcome 변화 0개. 새 55개 case 모두 통과 |
| 기존 세 실패의 내용 | 동일한 node ID·assertion/message·정규화된 traceback. 임시 디렉터리/객체 주소만 구분 |
| compileall / package·CLI import | 검증 복사본에서 성공 |
| 소스·기존 테스트·설정 보존 | 기존 274개 파일의 SHA256 일치. 패키지 Python source 110개 모두 무변경 |

기존 관련 파일은 `test_generation.py`, `test_crem_pipeline.py`, `test_synthetic_feasibility.py`, `test_score_diversity.py`, `test_combichem.py`, `test_database.py`, `test_rdkit_descriptors.py`, `test_crem_parent_pose.py`다. 전체 suite는 격리된 복사본에서 `python -m pytest -q -ra --junitxml=...`로 실행했다. 7개 skip은 기존과 같은 Torch 부재이며 이로 인해 LibInvent launcher 일부 실행 검증은 여전히 없다. 이 실행 시간을 성능 개선/저하로 해석하지 않는다.

원본 변경 파일:

- `tests/test_chemistry_contracts.py`: 관찰한 기존 동작의 characterization case만 추가.
- `docs/refactoring/T2A_ANALYSIS.md`: 이 분석·계획.
- 연결된 검증 증거 파일만 추가. **기존 runtime source, 기존 test/expectation, TOML, SPEC 및 DB/manifest는 수정하지 않았다.**

## 10. T2-B 진행 판단

현재 증거로는 **canonicalizer 한 함수 + `_core` wrapper만의 제한된 T2-B를 별도 승인 후 준비할 수 있다.** 다만 DB caller 전환/FP 이동까지 승인된 것으로 확대하지 않는다. 구현 전 새 leaf의 독립 import·배포와 wrapper 동등성 테스트를 추가해야 하며, saved source fingerprint 대상 수정 또는 진행 중 작업과의 배포 충돌이 확인되면 그 부분을 보류한다.

이번에 관찰한 None identity 처리의 타당성 검토는 별도 문제로 기록했다. 기존 3건을 유지했으며, 새로운 테스트 통과를 이 잠재 문제의 해결로 표현하지 않는다. 과학 계산 정책·fingerprint coverage·DB schema 변경을 pure function 분리와 함께 처리하지 않는다.

**T2-A에서 종료한다. T2-B의 실제 구현은 별도 승인 전에는 수행하지 않는다.**
