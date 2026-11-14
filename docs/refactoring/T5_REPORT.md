# T5 — 동일한 로컬 JobId JSON 저장 정책 통합

## 목적·변경·보존 인터페이스

`prime_gbsa_async._save`, `ligprep_recovery._save`, `glide_recovery.save_record`의 실제 구현은 동일했다: 각각 기존 job/record_file을 얻고 `.with_suffix('.tmp')`에 기본 `json.dumps` 문자열을 write_text한 뒤 replace한다. 경로 결정을 기존 wrapper에 남기고 이 세 줄만 `src/graft/_job_record.py::save_job_record`로 통합했다. 각 wrapper의 이름·signature·module·동적 경로 helper 조회를 보존했다.

변경 파일: 위 3개 module, 새 `_job_record.py`, 새 `tests/test_job_record_contracts.py`. 기존 공개 API 및 Legacy API 삭제는 없다. generic persistence framework는 만들지 않았다.

## 먼저 확보한 테스트와 결과

구조 변경 전 15개 characterization case를 추가했다. 세 wrapper 각각에 대해 Unicode/default JSON byte·순서·개행 없음, write→replace 순서, 이전 JobId 파일 보존, write/replace 예외 instance 전파, 임시 파일 잔존 여부, 직렬화 TypeError, 미존재 디렉터리의 FileNotFoundError 및 디렉터리를 만들지 않는 사실을 확인했다. fsync를 새로 호출하지 않는 기존 정책도 고정한다.

이 15개와 기존 Prime/Glide/LigPrep recovery·fresh calculation·lost-response 테스트를 합친 253개가 변경 전후 모두 통과했다. node outcome 변화 0개. 실패를 catch하거나 rollback·cleanup·durability를 강화하지 않았다. 최종 전체 검증은 T9에 기록한다.

## 합치지 않은 구현

| 대상 | 확인한 차이 / 판단 |
|---|---|
| amber_gbsa.save | 원래 확장자 뒤 `.pending`, indent=2, 마지막 newline. 기존 byte/temp 계약이 다르므로 유지 |
| combichem_slurm.atomic_save | `.pending`, sort_keys, flush+fsync. fingerprint 대상 및 durability가 달라 유지 |
| md_gbsa_native.save / native·remote 독립 helper | 독립 실행/업로드 의존성과 저장 정책이 다르므로 유지 |
| streaming SSH·batch pool·validation | 같은 이름/형태만으로 계약 동일성을 입증하지 못해 통합하지 않음 |

## Fingerprint·미검증·다음 단계

변경한 4개 runtime 파일은 조사한 implementation fingerprint 입력 목록 밖이다. native helper payload/source 및 guard/allowlist/manifest는 그대로다. 기존 세 module은 설치된 Graft package에서 사용되며 native upload 대상에 이 module을 파일 하나로 배포하는 참조는 발견하지 못했다. 새 `_job_record.py`를 포함한 package 배포가 필요하다. 외부 미제공 standalone 파일 복사 방식과 module.json 전체 재바인딩은 미검증이다. 실제 native 작업은 제출하지 않았다. T6으로 자동 진행했다.
