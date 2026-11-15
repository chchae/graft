# Source deployment and recovery

이번 요청은 실행 중 프로그램 중단을 허용했다. 해당 허용을 작업 데이터 삭제나 hash 우회 허용으로 해석하지 않았다. 테스트는 `/tmp/graft-t346-ii4pbdgh/project`에서 수행했다. 계산 작업을 강제 종료하거나 native 재제출하지 않았다.

## Signature 영향

- Slurm signature 입력인 config.py/database.py digest가 바뀐다. 기존 미완료 작업은 새 설치에서 source mismatch로 재개를 거부할 수 있다. fail-closed는 유지한다.
- CReM/remote/MD/Prime helper source, checksum 검증/allowlist, 기존 DB/manifest/JobId는 수정하지 않았다. 소스 목록 40개 중 38개는 동일하다.
- signature는 절대 source path도 포함한다. 다른 디렉터리에 backup을 설치하기만 해서는 원래 signature가 돌아온다고 보장할 수 없다.
- 같은 경로에서 실제 signature를 계산한 synthetic probe에서 old≠new, old=restored였다. production 작업의 cfg/input/native 상태까지 검증한 것은 아니다. 이전과 새 작업을 동일 설치에서 동시에 재개할 수 있다고 주장하지 않는다.

## 원본 경로 복구용 백업

Durable archive: `/homes1/chchae/work/GRAFT/graft/.git/refactoring-backups/graft-t346-ii4pbdgh/source-before.tar.gz`

Archive SHA256: `444ec576d4257e0340f555ce7df4dbf191587d960ad87ee6b27470816b1682be`

원본 src 전체를 보관했고 운영 데이터는 포함하지 않는다. source 적용 전 사용자 변경을 포함한 bytes이며 git HEAD로 돌아가는 백업이 아니다. 이후 수정이 생기면 자동 복원을 거부한다.

검사만 수행:

```bash
python /homes1/chchae/work/GRAFT/graft/.git/refactoring-backups/graft-t346-ii4pbdgh/restore_source.py
```

명시적인 source rollback이 필요할 때:

```bash
python /homes1/chchae/work/GRAFT/graft/.git/refactoring-backups/graft-t346-ii4pbdgh/restore_source.py --apply
```

이 스크립트는 archive/SHA와 변경된 기존 runtime 8개 파일이 저장된 before/after 중 하나인지 검사한 뒤 원래 경로에 원자적으로 복원한다. 새 project.py는 삭제하지 않고 원본 code에서 참조하지 않는 상태로 둔다. tests/docs나 어떤 DB/manifest/helper/native job도 수정하지 않는다. 새 import 경계 architecture test는 rollback 후 기대되지 않는 구조를 검사하므로 그대로 통과한다고 주장하지 않는다. 테스트/문서까지 되돌리려면 작은 patches와 별도 변경 기록을 검토해야 한다.

서비스/worker는 적절한 한 버전으로 다시 시작해야 하며 이미 import한 프로세스까지 자동 롤백하지 않는다. source 복원이 live job 완료/재개를 증명하거나 다른 입력/상태 guard를 우회하지 않는다. 이 작업에서는 실제 rollback/restart/relaunch를 실행하지 않았다.
