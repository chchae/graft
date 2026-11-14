# T6 — Orchestration 경계 두 곳의 독립 추출

## T6-A: Glide claim 정책

`stages.glide._run_stage` 안의 그룹 claim/filter loop를 `_claim_reference_groups(db_path, groups)`로 그대로 추출했다. 순서가 있는 groups를 순회하고, 각 그룹마다 기존 database.claim_compounds를 같은 expected/claimed state로 호출한다. 빈 claim은 제외하고 원래 compound row/order를 그대로 유지한다.

변경 파일: `src/graft/stages/glide.py`. `_run_stage(args, ns)`와 timed decorator, upstream/scaffold/reference 배정→timeout recovery→조회→fresh reference 조회→claim 순서를 보존했다. connection/claim transaction, 병렬 submit/collector, 그룹별 즉시 결과 commit, UNKNOWN JobId 보존·timeout 완료 판정·merge 순서는 바꾸지 않았다.

기존 integration/recovery 테스트가 이 경계를 실제 임시 SQLite와 event transcript로 검증한다. `test_glide_resources`에는 submit(2) < commit(1) < submit(3), 후속 제출 실패 시 결과 보존 및 동일 JobId 재개가 있다. 관련 142개가 전후 통과했고 node 변화는 0개다.

## T6-B: Driver pending 보고

`driver.supervise_iteration`의 pending 출력 block을 `_report_pending_stage`로 추출했다. helper가 이전과 같은 pending key를 반환한다. 제어 흐름의 worker 실행·완료 확인·once/stop·대기와 다음 stage 선택은 원래 함수에 남겼다.

변경 파일: `src/graft/driver.py`. rc/reason으로 routine poll 여부 결정→progress/print 선택→MD summary 조회/붙이기→stage elapsed/current time 조회→Amber 중복 억제→출력→key 갱신 순서를 보존했다. MD summary를 붙이기 전에 routine 여부를 결정하는 기존 의미도 그대로다. elapsed만 달라지는 경우의 Amber 억제, worker error 가시성·로그 문자열/verbosity/protocol은 변경하지 않았다.

관련 167개 결과는 전후 165 passed / known MD 로그 2 failed. node ID와 assertion 본문·message가 같다. 기대값 수정·xfail/skip·assertion 완화는 없다.

## 별도로 검토하고 보류한 함수

| 함수 | 실제 확인한 결합과 보류 이유 |
|---|---|
| _core.run_iteration_prime_gbsa | adam/local batch, legacy individual queue, native batch, legacy output copy→replace→JobId unlink의 서로 다른 회수 계약; 새 작업은 guard→ranking→입력/수용체 작성→create_batch→poll. 단순 dispatch 통합의 이득에 비해 원본 결과/JobId retirement 순서 위험이 큼. 기존 branch 유지 |
| md_gbsa.run | cache reuse, upstream native retirement/readiness, remote check→freeze selection, retry/recovery, uncertain task admission, replica 검증·progress 저장·compound commit·cleanup/registry 해제 순서가 하나의 복구 정책에 연결. helper 이동만으로 경계가 명확해진다는 증거가 부족해 유지 |
| write_elite_libinvent_training_files | 5/6-column legacy elites, score_source, vocabulary/token filtering, decoration 순서와 TSV/SMI 작성이 연결. torch 부재로 실제 TL launcher 7 case가 skip되고 과학적 입력 byte/모든 branch event 동등성 증거가 부족해 유지 |
| chemistry.scaffold.prepare_iteration_scaffold | 기존 snapshot/DB 복구, generation method 변경 거부, 초기 fallback 1회, 고정 MCS, 바로 이전 iteration elites와 pose ancestry가 연결. 현재 추출보다 과학적 보존 위험이 커 유지 |

## Fingerprint·남은 위험·다음 단계

적용한 driver/glide 파일은 조사한 source fingerprint 입력 밖이며 core/native helper는 이 단계에서 수정하지 않았다. DB/manifest/운영 작업 상태를 변경하지 않았다. 실제 native 동시 실행과 과학 계산은 미검증이며 mock/event tests를 그 증거로 주장하지 않는다. `_core` orchestration·LibInvent/scaffold 기술 부채는 남는다. T7을 계속 검토했다.
