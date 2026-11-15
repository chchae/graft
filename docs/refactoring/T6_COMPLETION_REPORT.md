# T6 후속 완료 — 복구·대상 확정·과학 입력 구성 경계

작성일: 2026-11-15 KST. main 9ec975f 이후의 사용자 미커밋·미추적 변경 및 앞선 T2/T6 변경을 포함한 시작 snapshot을 기준으로 한다. 별도 복사본에서 한 논리 경계씩 테스트한 뒤 반영했다. Git reset/기존 변경 덮어쓰기, production DB·manifest·JobId·결과 수정, native 계산 제출은 하지 않았다.

이전 T6 재개 작업은 [T6_RESTART_REPORT](T6_RESTART_REPORT.md)에 남겨 두었다. 이번에는 남아 있던 책임 경계를 추가로 분리했다. 알고리즘·scheduler·native 제출 정책은 바꾸지 않았다.

| 단계 | 새 private helper | 보존 내용 | 관련 변경 후 테스트 |
|---|---|---|---:|
| Prime | _core._recover_prime_gbsa_work | 3 saved mode+legacy 회수, JobId 파일 제거 시점, load 전에 capture한 async callback | 200 passed |
| Training | _core._elite_training_rows | 기존 분해/vocabulary 검사·skipped row·통계, prior load 전에 capture한 callback | 148 passed |
| MD | md_gbsa._freeze_new_md_selection | priority→rank→settings→remote 호환 확인→optional local prepare→atomic freeze | 254 passed |
| Scaffold | chemistry.scaffold._create_iteration_scaffold | N-1 elite 검사·원래 MCS/fallback/snapshot 구성, 이후 validation→DB→publish | 전후 216 passed |
| Glide | stages.glide._recover_reference_groups | native status/JobId/UNKNOWN·timeout 회수·결과 즉시 저장·reserved slots | 전후 142 passed |
| Driver | driver._report_completed_stage | 검증된 완료 이후 timer/log/report refresh, MD 재삽입 및 stage 진행 순서 | 전후 165 passed / 기존 MD 로그 2 failed |

Runtime 파일은 `_core.py`, `md_gbsa.py`, `chemistry/scaffold.py`, `stages/glide.py`, `driver.py`다. Glide의 기존 timed decorator는 원래 _run_stage에 남는다. collector thread·new submission loop를 다른 thread/worker로 이동하지 않았다. MD 저장 selection 회수·poll/result commit 루프도 유지했다. 순수 함수로 잘못 표현하거나 새로운 orchestration framework를 만들지 않았다.

`test_orchestration_followup_contracts.py` 12개를 먼저 추가해 기존 source에서 통과시켰다. Prime 2개는 load 중 rebind에도 기존 함수 capture를 유지하는지, training 1개는 prior load 중 token 함수 capture를, MD 9개는 각 오류 위치에서 remote check/local 준비/freeze/cleanup 순서와 예외 instance를 확인한다. 모두 native 계산은 mock했다. 기존 T6의 59 characterization 및 실제 RDKit/SQLite 기반 scaffold/recovery 테스트도 유지했다.

T6 시작 관련 323 passed. helper statement를 원래 caller 위치로 재삽입해 6개 대상 함수 전체 body의 AST가 전후 동일함을 확인했다. 이는 Python local scope/lifetime/traceback을 모두 보증하는 검증은 아니다. captured callback을 명시 인자로 전달하는 경우는 별도 테스트로 고정했다.

최종 **2,854 passed / 기존 3 failed / 7 skipped**, 기존 2,829 node 결과·실패 본문 변화 0, 이번 전체 추가 35개 통과. 기존 실패는 다음과 같다.

```text
tests/test_combichem_3d.py::test_real_probes_pass_but_actual_fragment_combination_collides_with_itself
tests/test_md_progress_summary.py::test_driver_pending_line_includes_progress_and_elapsed_even_on_worker_error[0]
tests/test_sequential_driver.py::test_pending_log_uses_persisted_iteration_stage_elapsed[md-gbsa-MD-GBSA still pending for compound 624598]
```

## Fingerprint·검증 범위·남은 사항

T6 수정 5개 파일 자체는 이번 source hash 목록 밖이며 native/helper payload는 변경하지 않았다. 함께 요청된 T3/T4로 config/database signature는 바뀐다. [복구·배포](t346/RECOVERY.md)의 제한이 적용된다. 공개·legacy signature, CLI 및 기존 로그/완료 조건은 보존했다.

mock 테스트는 실제 scientific accuracy나 다중 호스트 recovery를 입증하지 않는다. Prime 신규 계산 정책, MD poll/후처리, scientific MCS/분해 알고리즘과 global backend mutation(T7)은 별도 기술 부채로 남는다. 기존 실패 및 간헐 /proc 테스트 경쟁 조건도 수정하지 않았다. 요청 범위 T3/T4/T6를 완료하고 중단하며 다른 stage/backend 기능 개선·native 제출·GitHub push는 수행하지 않았다.

[전체 비교](t346/comparison.json), [최종 raw log](t346/final-full.log), [독립 패치](t346/patches/), [함수 길이 보조 기록](t346/function-lengths.json).
