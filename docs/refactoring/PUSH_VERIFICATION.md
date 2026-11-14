# GitHub push 전 리팩토링 변경 분리·검증

작성일: 2026-11-14. GitHub 기준 commit: `392674a481d41951e031f0ea2716db1e9f0271f9`.

이번 커밋은 T0–T9 리팩토링 패치·보존 테스트·계획/보고서만 포함한다. 다른 미커밋 기능 구현, 예제 설정·입력 삭제, 운영 출력/DB, macOS metadata는 포함하지 않는다. 파일 전체를 stage하는 대신 별도 Git index에 T1 import 패치와 T2/T5/T6/T8 패치를 순서대로 적용했다. 원본 작업 트리를 checkout/reset하지 않았다.

앞선 T0–T9 보고서는 당시 **다른 미커밋 기능을 포함한 로컬 작업 트리**의 검증 기록이다. 그 baseline은 git HEAD와 같지 않다. architecture.md의 reference-cache·최신 native-status 기능 관련 설명도 그 작업 트리의 문맥이며, 이 refactoring-only commit이 해당 별도 기능까지 제공한다는 뜻은 아니다. 배포 보류한 T3/T4 구현도 runtime에 적용하지 않았다.

따라서 push할 tree를 Git에서 새로 export하고 기존 HEAD export와 각각 별도 TMPDIR/registry에서 전체 테스트했다. 운영 설치·DB/manifest/native 작업은 변경하지 않았다.

| 검증 대상 | 결과 |
|---|---|
| 기존 GitHub HEAD | 2,522 passed / 기존 3 failed / 7 skipped |
| 리팩토링 커밋 후보 | 2,596 passed / 동일 3 failed / 7 skipped |
| 기존 2,532개 node 비교 | outcome·failure message·assertion 본문 변경 0개 |
| 추가 보존 테스트 | 74개 모두 통과: T0 2개 + T2-A 55개 + T5 15개 + leaf import 2개 |

[control 로그](push/control.log), [candidate 로그](push/candidate.log), [node 비교](push/comparison.json)에 결과를 남겼다. 테스트 수가 앞선 작업 트리의 2,718 passed와 다른 이유는 이번 커밋에 다른 미커밋 기능과 해당 테스트를 포함하지 않았기 때문이다. 테스트를 skip/xfail하거나 기대값을 수정하지 않았다. 주소·임시 경로와 pytest fixture header만 분리하여 비교했다.

원격 URL의 HTTPS 인증은 사용할 수 없어 기존 SSH 키의 chchae 계정 인증 및 같은 저장소의 push 권한을 확인했다. 영구 remote 설정을 바꾸지 않고 SSH push URL을 이번 명령에만 지정한다. 기존 미커밋 파일 내용은 보존한다.
