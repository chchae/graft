# T7 — Backend 전역 선택 검토와 보류

## 결론과 실제 조사

**런타임 변경 없음.** `combichem_distributed.install/uninstall` 및 remote.dispatch/stage.worker_loop의 전역 교체를 검토했고 이번 적용 범위에서는 보존했다.

실제 코드에서 _ORIGINAL_WORKER는 첫 stage.worker_loop를 기억하고 install은 module attribute를 바꾼다. uninstall은 저장된 worker로 복원한다. distributed.worker_loop는 Slurm, local/no-host 및 SSH 분기를 가지며, local fallback은 저장된 callable을 사용한다. 초기 Slurm 분기는 즉시 slurm_worker로 전달한다. 이후 원격 분기에는 stage.search 교체와 별도 Slurm 조건의 methyl_probe/select_mcs 교체 코드도 남아 있다. 후자의 조건은 초기 분기와 겹치므로 실행 가능성까지 일반화하지 않았고 이번에 제거하지 않았다. combichem_3d.run의 fork Process target은 module worker_loop이고 writer/result_writer 전달·orphan 복구의 순서와 결합된다. `dispatch`는 backend를 설정하고 helper source hash를 저장한다.

단순히 process target 한 곳만 인자로 바꾸면 local fallback, install/uninstall의 기존 import/patch 경계, writer 전달 및 child가 물려받는 계산 callable이 모두 보존된다는 증거가 없다. 명시적 선택 전환은 별도 집중 테스트와 fingerprint 배포 전환 정책이 필요한 작업으로 남긴다. 더 많은 클래스/추상화로 감싸지 않았다.

## 테스트·인터페이스

remote/Slurm/pipeline/workspace recovery 85개 통과. 기존 install→uninstall roundtrip·local/remote 동작·writer/Slurm recovery 검증을 유지한다. API·multiprocessing start method·worker signature·remote protocol·기존 설정은 변경하지 않았다. 실제 SSH/Slurm/native job 및 새로운 OS/start method는 검증하지 않았다.

## Fingerprint·보류 이유·후속

관련 combichem*.py들은 CReM/standalone/Slurm source fingerprint와 remote-implementation-sha256에 직접 들어간다. 구현을 교체하면 기존 저장 fingerprint가 변한다. 호환 allowlist·guard 우회·manifest/DB 수정은 금지되어 있으므로 이번에는 유지한다. 배포 정책 결정 없이 안전한 다른 작업 T8·T9로 계속 진행했다. 변경 파일은 이 보고서뿐이다.
